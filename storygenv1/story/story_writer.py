from copy import deepcopy
from functools import partial
import logging
import os
import pickle

from storygenv1.plan.outline import *
from storygenv1.story.story import *

def generate_story(plan, story_config, story_prompts, llm_client):
    beam = StoryBeam([Story(plan)])
    step = 0

    while True:
        try:
            node_to_render = select_node_to_render(plan, beam, story_config)
            logging.info(f"Rendering node: {node_to_render.text}")
        except StopIteration as e:
            logging.info(f"StopIteration exception: {e}")
            break
        next_story_candidates = []
        for story in beam:
            next_story_candidates += render_node(story, node_to_render, story_config, story_prompts, llm_client).stories
        beam = filter_beam(StoryBeam(next_story_candidates), beam_width=story_config['outline_node_beam_width'], aux_attr='score')

        #logging.info(f"Best story: {str(beam.stories[0])}")
        step += 1
        
    beam = end_story(beam, plan, story_config, story_prompts, llm_client)
    #logging.info(f"Best story and ending: {str(beam.stories[0])}")
    return beam

def select_node_to_render(plan, beam, story_config):
    if story_config['rendering_policy'] == 'all':
        for node in plan.outline.depth_first_traverse():
            if node not in beam.rendered_nodes():
                return node
        raise StopIteration
    elif story_config['rendering_policy'] == 'leaves':
        for node in plan.outline.leaves():
            if node not in beam.rendered_nodes():
                return node
        raise StopIteration
    else:
        raise NotImplementedError

def update_best_stories(i, best_stories, beam, story_config, aux_attr='score'):
    if i < story_config['min_passages_per_node']:
        return beam
    else:
        # look for best scores from current passage list
        stories_with_scores = []
        for story in beam.stories + best_stories.stories:
            # scores = story.passage_lists[-1].aux_attr_list(aux_attr)
            # score = max(scores)
            score = story.final_passage_aux_attr(aux_attr)
            stories_with_scores.append((story, score))
        stories_with_scores = sorted(stories_with_scores, key=lambda x: x[1], reverse=True)
        best_stories = StoryBeam([story for story, _ in stories_with_scores[:story_config['passage_beam_width']]])
        return best_stories

def render_node(story, node_to_render, story_config, story_prompts, llm_client, **kwargs):
    logging.info(f"render_node: {node_to_render.text}")
    node_passage_list = OutlineNodePassageList(node_to_render)
    story = story.copy_append_list(node_passage_list)
    beam = StoryBeam([story])
    best_stories = StoryBeam([story])
    for i in range(story_config['max_passages_per_node']):
        updated_stories = []
        for story in beam:
            if i > len(story.passage_lists[-1]):
                # no need to continue generating for story candidates that have already ended generation for this node
                continue
            passages = render_passage(story, node_to_render, story_config, story_prompts, llm_client, **kwargs)
            for passage in passages:
                updated_stories.append(story.copy_append_passage(passage))
        beam = filter_beam(StoryBeam(updated_stories), beam_width=story_config['passage_beam_width'], aux_attr='score')
        best_stories = update_best_stories(i, best_stories, beam, story_config)
    return best_stories

def render_passage(story, node_to_render, story_config, story_prompts, llm_client, **kwargs):
    # information about ancestors in premise
    ancestors = ' '.join([ancestor.text for ancestor in node_to_render.ancestors(include_self=False)]) if story_config.get('ancestor_nodes_in_premise', False) else ''
    if len(ancestors) > 0:
        ancestors = f'at a high level, {ancestors.strip()} More concretely, '

    # entity descriptions to include in context
    if len(story.passage_lists) >= 2 and story_config.get('previous_node_entity_descriptions', False):
        entities_to_describe = story.passage_lists[-2].outline_node.entities
        for entity in node_to_render.entities:
            if entity not in entities_to_describe:
                entities_to_describe.append(entity)
    else:
        entities_to_describe = node_to_render.entities
    entity_descriptions = [story.plan.entity_list.get_entity_by_name(entity).description for entity in entities_to_describe]

    # previous nodes' events
    if len(story.passage_lists) < 2:
        previous_node_events = 'N/A'
    else:
        if story_config['collapse_previous_events']:
            # for all previous nodes which can be collapsed into their parents, collapse them to save context window space
            all_previous_nodes = set(story.rendered_nodes()[:-1])
            all_collapsed_ancestors = [node for node in story.plan.outline.depth_first_traverse(include_self=False) if all([n in all_previous_nodes for n in node.leaves()])]
            previous_nodes = [node for node in all_collapsed_ancestors if node.parent is not None and node.parent not in all_collapsed_ancestors]
        else:
            previous_nodes = story.rendered_nodes()[:-1]
        previous_node_events = ' '.join([node.text for node in previous_nodes])
    
    if len(story.passage_lists) < 2:
        previous_scene_info = ''
    else:
        previous_scene_info = f' The setting is previously {story.passage_lists[-2].outline_node.scene}'
        
    # summary of previous context
    if story_config['previous_summary_context'] == 'previous-node':
        if len(story.passage_lists) < 2:
            previous_summary = 'N/A'
        else:
            raw_context = str(story.passage_lists[-2])
            previous_summary = llm_client.call_with_retry(
                story_prompts['summary'].format(
                    raw_context=raw_context
                ),
                SamplingConfig.from_config(story_config['summary']),
                filter=min_max_tokens_filter(0, story_config['summary']['max_tokens'])
            )[0]
    else:
        raise NotImplementedError

    # TODO fix spacing to LLaMA tokenizer removing spacing at beginning
    # any previous events to include in description of upcoming
    previous_events = ''
    previous_nodes = reversed(story.rendered_nodes()[:-1])
    for i in range(story_config['include_previous_events']):
        if i >= len(previous_nodes):
            break
        previous_events = previous_nodes[i].text + ' ' + previous_events
    if len(previous_events) > 0:
        previous_events = previous_events + ' '
    
    # any future events to include in description of upcoming
    future_events = ''
    future_story = story
    for i in range(story_config['include_next_events']):
        try:
            next_node = select_node_to_render(future_story.plan, future_story, story_config) # technically this is only guessing based on empty passages
            future_events += next_node.text + ' '
            future_story = future_story.copy_append_list(OutlineNodePassageList(next_node))
        except StopIteration:
            break
    if len(future_events) > 0:
        future_events = ' ' + ' '.join(future_events)

    # raw text for autoregressively continuing generation
    if story_config['autoregressive_context'] == 'current-node':
        if len(story.passages()) == 0:
            autoregressive_context = 'Chapter 1\n\n'
        elif len(story.passage_lists[-1].passages) == 0:
            autoregressive_context = story.passage_lists[-2].passages[-1].text # most recent passage if the current node is the first passage of a new node
        else:
            autoregressive_context = str(story.passage_lists[-1])
    else:
        raise NotImplementedError
   
    if kwargs.get('is_ending', False):
        ending_info = ' Do NOT write any extra comments, suggestions, or questions at the end.'
    else:
        ending_info = ' This passage should end the story.' 
        
    sampling_config = SamplingConfig.from_config(story_config['passage'])
    text_filter = Filter(lambda s: len(s.text.strip()) > 0 and not any([
        bad_string.lower() in s.text.lower() for bad_string in [
            'passage', '*', '[', ']', 'chapter', 'continued', '...'
        ]
    ]))

    passages = llm_client.call_with_retry(
        story_prompts['passage'].format(
            premise=story.plan.premise.premise,
            ancestors=ancestors,
            entity_descriptions=' '.join(entity_descriptions),
            previous_node_events=previous_node_events,
            previous_summary=previous_summary,
            previous_events=previous_events,
            previous_scene_info=previous_scene_info,
            current_event=node_to_render.text,
            future_events=future_events,
            current_scene = node_to_render.scene,
            current_entities=', '.join(node_to_render.entities),
            autoregressive_context=autoregressive_context,
            ending_info=ending_info
        ),
        sampling_config,
        postprocessor=partial(make_and_score_passages, 
                            story=story, 
                            node=node_to_render, 
                            story_config=story_config, 
                            story_prompts=story_prompts, 
                            llm_client=llm_client,
                            is_ending=kwargs.get('is_ending', False)
        ),
        filter=text_filter,
        empty_ok=True,
    )

    if not passages:
        logging.warning("No passages generated from LLM")
        return []

    if not isinstance(passages, list):
        passages = [passages]
            
    #logging.info(f"render_passage. Passages length: {len(passages)}")
    return passages


def make_and_score_passages(raw_passages, story, node, story_config, story_prompts, llm_client, full_completion_object=None, **kwargs):
    assert len(full_completion_object['choices']) == len(raw_passages)

    passages = []
    for passage_idx, passage_text in enumerate(raw_passages):
        passage_text = passage_text.rstrip()
        if story_config.get('include_prefix_space', False) and not passage_text.startswith(' '):
            passage_text = ' ' + passage_text
            passage_text = passage_text.replace('  ', ' ')
        aux_info = {}
        score = 0
        for scorer in story_config['score']['scorers']:
            if scorer == 'coherence':
                coherence_score = 0
                if len(story.passages()) > 0:
                    try:
                        coherence_prefix = story.passages()[-story_config['score']['coherence']['max_prefix_passages']:]
                        coherence_prefix = ''.join([p.text for p in coherence_prefix])
                        _, coherence_score_completion = llm_client.call_with_retry(
                            story_prompts['score']['coherence'].format(
                                prefix=coherence_prefix.strip(),
                                continuation=passage_text.strip()
                            ),
                            SamplingConfig.from_config(story_config['score']['coherence']),
                            filter=lambda s: len(s.strip()) > 0,
                            return_full_completion=True
                        )
                        yes_no_logprobs = extract_choice_logprobs(coherence_score_completion, default_logprobs=[-1e8, -1e7])
                        coherence_score = yes_no_logprobs[0][0] # logprob of yes
                    except:
                        logging.warning(f"Failed to score coherence for passage: {passage_text}")
                        coherence_score = -1e10
                score += coherence_score
                aux_info['coherence_score'] = coherence_score
            elif scorer == 'relevance':
                try:
                    _, relevance_score_completion = llm_client.call_with_retry(
                        story_prompts['score']['relevance'].format(
                            node_event=node.text.strip(),
                            continuation=(''.join([p.text for p in story.passage_lists[-1].passages] + [passage_text])).strip(),
                        ),
                        SamplingConfig.from_config(story_config['score']['relevance']),
                        filter=lambda s: len(s.strip()) > 0,
                        return_full_completion=True
                    )
                    yes_no_logprobs = extract_choice_logprobs(relevance_score_completion, default_logprobs=[-1e8, -1e7])
                    relevance_score = yes_no_logprobs[0][0] # logprob of yes
                except:
                    logging.warning(f"Failed to score relevance for passage: {passage_text}")
                    relevance_score = -1e10
                score += relevance_score
                aux_info['relevance_score'] = relevance_score
            elif scorer == 'commentary':
                if any([s in passage_text for s in story_config['passage']['stop']]):
                    commentary_score = -1e10
                else:
                    try:
                        _, commentary_score_completion = llm_client.call_with_retry(
                            story_prompts['score']['commentary'].format(
                                last_paragraph=passage_text.rsplit('\n', 1)[-1].strip()
                                # continuation=passage_text
                            ),
                            SamplingConfig.from_config(story_config['score']['commentary']),
                            filter=lambda s: len(s.strip()) > 0,
                            return_full_completion=True
                        )
                        story_commentary_logprobs = extract_choice_logprobs(commentary_score_completion, choices=['A', 'B'], default_logprobs=[-1e8, -1e7], case_sensitive=True)
                        commentary_score = story_commentary_logprobs[0][0] # logprob of A (it's asking whether it's story or commentary; we want it to be a story)
                    except Exception as e:
                        logging.warning(f"Failed to score commentary for passage: {passage_text}, error: {e}")
                        commentary_score = -1e10
                score += commentary_score
                aux_info['commentary_score'] = commentary_score
            elif scorer == 'length':
                length_score = 0
                if full_completion_object['choices'][passage_idx]['finish_reason'] != 'length':
                    if 'is_ending' in kwargs and kwargs['is_ending']:
                        # we want to stop early when trying to end the story
                        length_score = 100
                    else:
                        # penalize for not finishing due to length - we don't want things that stopped early instead of continuing in the story text style.
                        length_score = -100
                score += length_score
                aux_info['length_score'] = length_score
            else:
                raise NotImplementedError
        aux_info['score'] = score
        #logging.info(f"Scored passages: {passage_text}, Total score: {score}")
        passages.append(Passage(passage_text + "\n", aux_info))
  
    return passages

def filter_beam(beam: StoryBeam, beam_width: int = 1, aux_attr: str = 'score') -> StoryBeam:
    if len(beam.stories) <= 1:
        return beam
        
    scored_story_candidates = []
    for candidate in beam.stories:
        try:
            score = candidate.final_passage_aux_attr(aux_attr)
            scored_story_candidates.append((candidate, score))
        except Exception as e:
            logging.error(f"Error getting score for story: {str(e)}")
            continue
            
    if not scored_story_candidates:
        logging.warning("No valid scored candidates found")
        return beam
    
    sorted_story_candidates = sorted(
        scored_story_candidates, 
        key=lambda x: x[1], 
        reverse=True
    )
    
    # for i, (story, score) in enumerate(sorted_story_candidates):
    #     logging.info(f"Rank {i+1}: Score {score}")
        
    filtered_story_candidates = [
        candidate for candidate, _ in sorted_story_candidates[:beam_width]
    ]

    return StoryBeam(filtered_story_candidates)

def end_story(beam, plan, story_config, story_prompts, llm_client):
    if story_config['ending_policy'] == 'none':
        pass
    elif story_config['ending_policy'] == 'append-passage':
        new_stories = []
        for story in beam:
            passages = render_passage(story, story.rendered_nodes()[-1], story_config, story_prompts, llm_client, is_ending=True)
            new_stories.append(story.copy_append_passage(passages[0]))
            logging.info(f"New story: {str(new_stories[-1])}, len(new_stories): {len(new_stories)}")
        beam = StoryBeam(new_stories)
    elif story_config['ending_policy'] == 'append-node':
        plan = deepcopy(plan)
        previous_node = beam.rendered_nodes()[-1]
        end_node = OutlineNode('The conclusion of the story.', plan.outline, scene=previous_node.scene, entities=previous_node.entities)
        plan.outline.children.append(end_node)
        next_story_candidates = []
        for story in beam:
            next_story_candidates += render_node(story, end_node, story_config, story_prompts, llm_client, is_ending=True).stories
        beam = filter_beam(StoryBeam(next_story_candidates), story_config['outline_node_beam_width'], aux_attr='score')
    else:
        raise NotImplementedError
    return beam.right_truncate(story_config['ending_stop']) if 'ending_stop' in story_config else beam
        
def extend_by_last_node(plan, story_config, story_prompts, llm_client):
    beam = StoryBeam([Story(plan)])
    parent_node = plan.outline.children[-1]
    
    leaf_nodes = list(parent_node.leaves())
    for node in leaf_nodes:
        if node not in beam.rendered_nodes():
            logging.info(f"Rendering node: {node.text}")
            next_story_candidates = []
            for story in beam:
                next_story_candidates += render_node(story, node, story_config, story_prompts, llm_client).stories
            
            beam = filter_beam(StoryBeam(next_story_candidates), beam_width=story_config['outline_node_beam_width'], aux_attr='score')
            #logging.info(f"Best story: {str(beam.stories[0])}")
    return beam
