from functools import partial
import random
from typing import Dict
from storygenv1.common.util import *
from storygenv1.plan.plan import Plan
from storygenv1.premise.premise import Premise
from storygenv1.plan.setting import Setting
from storygenv1.plan.entity import *
from storygenv1.plan.outline import *
from storygenv1.common.llm.llm import SamplingConfig
import string

def generate_setting(plan, llm_client, setting_prompt, setting_config):
    plan.setting = Setting(llm_client.call_with_retry(
        setting_prompt.format(title=plan.premise.title, premise=plan.premise.premise),
        SamplingConfig.from_config(setting_config),
        filter=min_max_tokens_filter(0, setting_config['max_tokens']))[0]
    )
    logging.info(f"Generated setting: {plan.setting.setting}")
    return plan

def postprocess_name(names, **kwargs):
    invalid_prefixes = [
        "here are",
        "let's",
        "as the",
        "continuing",
        "next",
        "the following",
        "these are",
        "some potential",
        "the following are",
        "missing details",
        "here is the",
        "i'll continue",
        "however",
        "sure",
        "full name:",
        "[inst]",
        "[echo]",
        "category",
        "title",
        "generated",
    ]

    valid_names = []
    for name in names:
        name = name.strip(string.whitespace + string.punctuation + '-')
        name = ''.join(c for c in name if c.isalnum() or c.isspace() or '\u4e00' <= c <= '\u9fff')
        
        if "Full name:" in name:
            name = name.split("Full name:")[-1]
        if "Description:" in name:
            name = name.split("Description:")[0]
        name = name.strip()

        if not name:
            continue

        name_lower = name.lower()
        if any(name_lower.startswith(prefix) for prefix in invalid_prefixes):
            continue
        invalid_words = ["character", "entity", "story", "major", "following", "remaining", "location", "inst", "echo", "as", "not", "premise"]
        if any(word in name_lower for word in invalid_words):
            continue

        if any('\u4e00' <= c <= '\u9fff' for c in name):
            # 中文名字
            words = name.split()
            if (len(''.join(words)) >= 2 and
                not name.isdigit() and
                any('\u4e00' <= c <= '\u9fff' for c in name)):
                valid_names.append(' '.join(words))
        else:
            name = ' '.join(word.capitalize() for word in name.split())
            words = name.split()
            if (len(words) >= 1 and 
                not name.isdigit() and 
                not any(len(word) == 1 for word in words) and
                all(word[0].isupper() and len(word) > 1 for word in words) and
                any(c.isalpha() for c in name)):
                valid_names.append(name)

    return valid_names

def postprocess_entity_description(descriptions, **kwargs):
    responses = []
    
    invalid_phrases = [
        "here is",
        "character description",
        "to complete the sentence",
        "continuing with",
        "let me provide",
        "description of",
        "here's the description"
    ]
    
    for description in descriptions:
        try:
            if '\n' in description:
                description = description.split('\n')[0]
            description = description.strip()
            
            if '[INST]' in description:
                description = description.split('[INST]')[0].strip()
            
            name = kwargs.get('name', '')
            if name:
                pattern = f"{name} is {name} is"
                if pattern in description:
                    description = description.replace(pattern, "")
                pattern = f"{name} is"
                if description.startswith(pattern):
                    description = description[len(pattern):].strip()
            
            if ': ' in description:
                before_colon, after_colon = description.split(': ', 1)
                if before_colon in kwargs.get('name', ''):
                    description = after_colon
            
            description_lower = description.lower()
            if any(phrase in description_lower for phrase in invalid_phrases):
                continue
            
            description = description.strip(' .:')
            
            if (description and 
                len(description) > 128 and
                not any(phrase in description_lower for phrase in invalid_phrases)):
                if not description.endswith('.'):
                    description += '.'
                responses.append((description, True))
                
        except Exception as e:
            logging.error(f"Error processing description: {str(e)}")
            continue
            
    return responses

def generate_main_character(plan, llm_client, entity_prompt, entity_config) -> bool:
    max_attempts = 2
    attempts = 0

    name_config, description_config = entity_config['name'], entity_config['description']
    name_prompt, description_prompt = entity_prompt['main_character_name'], entity_prompt['main_character_description']
    while attempts < max_attempts:
        name_result = llm_client.call_with_retry(
            name_prompt.format(
                title=plan.premise.title, 
                premise=plan.premise.premise,
            ),
            SamplingConfig.from_config(name_config),
            postprocessor=postprocess_name,
            filter=word_filter([entity.name for entity in plan.entity_list] + ['full', 'Full', 'name', 'Name']) + \
                    min_max_tokens_filter(0, name_config['max_tokens'])
        )
        attempts+=1
        if not name_result or not name_result[0]:
            logging.warning(f"Invalid main character name generated (attempt {attempts})")
            continue
        main_name = name_result[0]

        description_result = llm_client.call_with_retry(
                description_prompt.format(
                    title=plan.premise.title, 
                    premise=plan.premise.premise,
                    setting=plan.setting.setting,
                    previous_entities=str(plan.entity_list),
                    current_number=len(plan.entity_list) + 1,
                    name=main_name
                ),
                SamplingConfig.from_config(description_config),
                postprocessor=postprocess_entity_description,
                filter=wrap_filter_for_tuple(
                    min_max_tokens_filter(0, description_config['max_tokens']) + \
                    Filter(lambda s: s.endswith('.'))
                )
            )

        if not description_result:
                logging.warning(f"Failed to generate main character description (attempt {attempts})")
                continue
        main_description, _ = description_result[0]

        if any(invalid in main_description.lower() for invalid in [
            "here's", "continuing", "let's", "following", "as requested"
        ]):
            logging.warning(f"Invalid description content (attempt {attempts})")
            continue

        # add main character to list
        plan.entity_list.entities.append(Entity(main_name, main_description))
        return True

def generate_entities(plan, llm_client, entity_prompt, entity_config):
    name_config, description_config = entity_config['name'], entity_config['description']
    plan.entity_list = EntityList()

    generate_main_character(plan, llm_client, entity_prompt, entity_config)

    max_attempts = 15
    attempts = 0

    #generate other entities
    name_prompt= entity_prompt['name']
    ally_description_prompt = entity_prompt['ally_description']
    adversary_description_prompt = entity_prompt['adversary_description']
    prompts = [ally_description_prompt, ally_description_prompt, adversary_description_prompt, adversary_description_prompt,adversary_description_prompt]
    prompts_index = 0
    while len(plan.entity_list) < entity_config['min_entities'] and attempts < max_attempts:
        attempts +=1
        try:
            entity_name = llm_client.call_with_retry(
                name_prompt.format(
                    title=plan.premise.title, 
                    premise=plan.premise.premise,
                    setting=plan.setting.setting,
                    previous_entities=plan.entity_list.print_with_full_names(),
                    current_number=len(plan.entity_list) + 1
                ),
                SamplingConfig.from_config(name_config),
                postprocessor=postprocess_name,
                filter=word_filter([entity.name for entity in plan.entity_list] + ['full', 'Full', 'name', 'Name']) + \
                        min_max_tokens_filter(0, name_config['max_tokens'])
            )
            if not entity_name or not entity_name[0]:
                logging.warning(f"Invalid entity name generated (attempt {attempts})")
                continue
            entity_name = entity_name[0]

            description_prompt = prompts[prompts_index]
            prompts_index = (prompts_index + 1) % len(prompts)
            description_result = llm_client.call_with_retry(
                description_prompt.format(
                    title=plan.premise.title, 
                    premise=plan.premise.premise,
                    setting=plan.setting.setting,
                    previous_entities=str(plan.entity_list),
                    current_number=len(plan.entity_list) + 1,
                    name=entity_name
                ),
                SamplingConfig.from_config(description_config),
                postprocessor=postprocess_entity_description,
                filter=wrap_filter_for_tuple(
                    min_max_tokens_filter(0, description_config['max_tokens']) + \
                    Filter(lambda s: s.endswith('.'))
                )
            )
            if not description_result:
                logging.warning(f"Failed to generate description (attempt {attempts})")
                continue
            entity_description, _ = description_result[0]

            # validate description contents
            if any(invalid in entity_description.lower() for invalid in [
                "here's", "continuing", "let's", "following", "as requested"
            ]):
                logging.warning(f"Invalid description content (attempt {attempts})")
                continue

            # add entity to list
            plan.entity_list.entities.append(Entity(entity_name, entity_description))

            if len(plan.entity_list) >= entity_config['max_entities']:
                break

        except Exception as e:
            logging.error(f"Error in entity generation (attempt {attempts})")
            continue

    if len(plan.entity_list) < entity_config['min_entities']:
        raise Exception(f"Failed to generate minimum number of entities. Generated: {len(plan.entity_list)}, Required: {entity_config['min_entities']}")
    
    logging.info(f"All entities: {[{'name': e.name, 'description': e.description} for e in plan.entity_list]}")
    return plan

def generate_outline(plan, llm_client, outline_prompt, outline_config):
    plan.outline = OutlineNode('', None)
    while True:
        try:
            node_to_expand = select_node_to_expand(plan.outline, outline_config)
        except StopIteration:
            break
        generate_node_subevents(node_to_expand, llm_client, outline_prompt, outline_config, plan)
    return plan

def event_postprocessor(events, has_next_indicator, current_number, **kwargs):
    combined_text = '\n'.join(events)
    lines = combined_text.split('\n')
    
    is_depth_0 = current_number.startswith('1.')
    expected_events = 3 if is_depth_0 else 1
    
    found_events = []
    current_event = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if re.match(r'^\d+\.', line):
            if current_event:
                found_events.append(' '.join(current_event))
                current_event = []
            current_event.append(line)
        else:
            current_event.append(line)
    
    if current_event:
        found_events.append(' '.join(current_event))
    
    responses = []
    for i, event in enumerate(found_events, 1):
        try:
            event = event.strip()
            if event.startswith(f"{i}. "):
                event = event[len(f"{i}. "):]
            
            if event and event[-1] not in ['.', '?', '!']:
                event += '.'
            
            words = event.split()
            if 6 <= len(words) <= 30:
                has_next = i < len(found_events)
                responses.append((event, has_next))
            else:
                logging.warning(f"Event {i} length not in range: {len(words)} words")
                if is_depth_0:
                    return []
        except Exception as e:
            logging.error(f"Error processing event {i}: {str(e)}")
            if is_depth_0:
                return []
    
    if is_depth_0 and len(responses) != expected_events:
        logging.warning(f"Expected {expected_events} events, got {len(responses)}")
        return []
    
    return responses

def generate_node_subevents(node, llm_client, outline_prompt, outline_config, plan):
    if node.depth() == 0:
        event_config = outline_config['event_depth_0']
        event_prompt = outline_prompt['event_depth_0']
    else:
        event_config = outline_config['event']
        event_prompt = outline_prompt['event']
    
    has_next = True
    while has_next and len(node.children) < outline_config['max_children']:
        new_child = OutlineNode('', node)
        node.children.append(new_child)
        context_prefix, context_suffix = new_child.context(outline_config['context'])
        
        filter = wrap_filter_for_tuple(min_max_tokens_filter(0, event_config['max_tokens']) + 
                                     word_filter(['[', 'TODO', ']', ':']))
        
        event, event_has_next = llm_client.call_with_retry(
            event_prompt.format(
                title=plan.premise.title,
                premise=plan.premise.premise,
                setting=plan.setting.setting,
                entities=str(plan.entity_list),
                formatted_current_number=new_child.number().rstrip(),
                stripped_current_number=new_child.number().strip(),
                context_prefix=context_prefix,
                context_suffix=context_suffix,
                predecessor_info=f'describing the beginning of "{new_child.predecessor().text}"' if len(node.children) == 1 else f'describing the conclusion of "{node.text}" after "{new_child.predecessor().text}"',
                successor_info=f'but before "{new_child.successor().text}"' if new_child.successor() is not None else 'The upcoming event(s) are the conclusion of the whole story, so make sure to wrap things up nicely.',
                preferred_max_children=outline_config['preferred_max_children'],
            ),
            SamplingConfig.from_config(event_config),
            postprocessor=partial(event_postprocessor, has_next_indicator='\n' + new_child.number(lookforward=1).strip(), current_number=new_child.number(lookforward=0).strip()),
            filter=filter,
        )[0]

        new_child.text = event
        logging.info(f"Generated event for node {new_child.number()}: {event}")
        
        generate_node_scene(new_child, llm_client, outline_prompt['scene'], outline_config['scene'], plan)
        generate_node_entities(new_child, llm_client, outline_prompt['entity'], outline_config['entity'], plan)

        if len(node.children) < outline_config['min_children']:
            has_next = True
        else:
            has_next = event_has_next
            if len(node.children) >= outline_config['max_children']:
                has_next = False
    return plan

def scene_postprocessor(scenes, **kwargs):
    responses = []
    for scene in scenes:
        scene = scene.lstrip()
        scene = scene.split('\n')[0].rstrip()
        scene = scene.split('Characters:')[0].rstrip()
        scene = scene.split('Scene:')[-1].lstrip()
        if '"' in scene:
            scene = scene[:scene.index('"')]
        responses.append(scene)
 
    return responses

def generate_node_scene(node, llm_client, scene_prompt, scene_config, plan):
    context_prefix, context_suffix = node.context(scene_config['context'])
    node.scene = llm_client.call_with_retry(
        scene_prompt.format(
            title=plan.premise.title,
            premise=plan.premise.premise,
            setting=plan.setting.setting,
            entities=str(plan.entity_list),
            formatted_current_number=node.number().rstrip(),
            stripped_current_number=node.number().strip(),
            current_event=node.text,
            context_prefix=context_prefix,
            context_suffix=context_suffix,
        ),
        SamplingConfig.from_config(scene_config),
        postprocessor=scene_postprocessor,
        filter=min_max_tokens_filter(0, scene_config['max_tokens']) + word_filter(['[', 'TODO', ']', ':'])
    )[0]
    logging.info(f"Generated scene for node {node.number()}: {node.scene}")

def entity_postprocessor(predicted_entities_lists, entity_list, already_detected_entities, **kwargs):
    if not predicted_entities_lists:
        logging.warning("Received empty entities list")
        return [already_detected_entities]
        
    valid_entity_names = {e.name for e in entity_list}

    instruction_phrases = {
        "here are",
        "let's",
        "as the",
        "continuing",
        "next",
        "the following",
        "these are",
        "some potential",
        "the following are",
        "missing details",
        "here is the",
        "i'll continue",
        "here's a"
    }
    
    responses = []
    for entities in predicted_entities_lists:
        try:
            entities = entities.split('\n')[0].rstrip()
            if entities.endswith('.'):
                entities = entities[:-1]
                
            entity_candidates = [e.strip() for e in entities.split(',')]
            
            valid_entities = []
            for entity in entity_candidates:
                entity_lower = entity.lower()
                if any(phrase in entity_lower for phrase in instruction_phrases):
                    continue
                clean_entity = entity.strip(' ."\'')
                if clean_entity in valid_entity_names:
                    valid_entities.append(clean_entity)

            dedup_entities = []
            for entity in valid_entities:
                if entity not in dedup_entities and entity not in already_detected_entities:
                    dedup_entities.append(entity)

            final_entities = already_detected_entities + dedup_entities
            if final_entities:
                responses.append(final_entities)
            
        except Exception as e:
            logging.error(f"Error processing entities: {str(e)}")
            if already_detected_entities:
                responses.append(already_detected_entities)
            
    if not responses and already_detected_entities:
        responses.append(already_detected_entities)
    elif not responses:
        responses.append([])
        
    return responses

def generate_node_entities(node, llm_client, entity_prompt, entity_config, plan):
    context_prefix, context_suffix = node.context(entity_config['context'])

    detected_entities = detect_entities(node.text, plan.entity_list)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            entities = llm_client.call_with_retry(
                entity_prompt.format(
                    title=plan.premise.title,
                    premise=plan.premise.premise,
                    setting=plan.setting.setting,
                    entities=str(plan.entity_list),
                    formatted_current_number=node.number().rstrip(),
                    stripped_current_number=node.number().strip(),
                    current_event=node.text,
                    current_scene=node.scene,
                    context_prefix=context_prefix,
                    context_suffix=context_suffix,
                    detected_entities=', '.join(detected_entities) if detected_entities else '',
                ),
                SamplingConfig.from_config(entity_config),
                postprocessor=partial(entity_postprocessor, entity_list=plan.entity_list, already_detected_entities=detected_entities),
                filter=Filter(lambda l: len(l) > 0),
            )[0]

            if entities and any(e in [ent.name for ent in plan.entity_list] for e in entities):
                node.entities = entities
                return
            logging.warning(f"Attempt {attempt + 1}: No valid entities generated, retrying...")

        except Exception as e:
            logging.warning(f"Attempt {attempt + 1} failed: {str(e)}")
    
    logging.warning(f"All attempts to generate entities failed for node {node.number()}")
    if detected_entities:
        node.entities = detected_entities
    elif node.predecessor() and hasattr(node.predecessor(), 'entities'):
        node.entities = node.predecessor().entities.copy()
    else:
        node.entities = []

def select_node_to_expand(outline, outline_config):
    if outline_config['expansion_policy'] == 'breadth-first':
        for node in outline.breadth_first_traverse(max_depth=outline_config['max_depth']-1):
            if len(node.children) == 0:
                return node
        raise StopIteration
    else:
        raise NotImplementedError

def plan_list_extend_plots(plan, llm_client, prompts, config):
    if not plan.outline:
        raise ValueError("Plan outline is empty")

    plots = []
    try:
        new_node = OutlineNode('', plan.outline)
        plan.outline.children.append(new_node)

        event_config = config['event_depth_0']
        event_prompt = prompts['event_depth_0']
        context_prefix, context_suffix = new_node.context(config['context'])
        
        event_result = llm_client.call_with_retry(
            event_prompt.format(
                title=plan.premise.title,
                premise=plan.premise.premise,
                setting=plan.setting.setting,
                entities=str(plan.entity_list),
                formatted_current_number=new_node.number().rstrip(),
                stripped_current_number=new_node.number().strip(),
                context_prefix=context_prefix,
                context_suffix=context_suffix,
                predecessor_info=f'describing the beginning of a new chapter' if not new_node.predecessor() else f'continuing after "{new_node.predecessor().text}"',
                successor_info='This will be the start of new events.',
                preferred_max_children=config['preferred_max_children'],
            ),
            SamplingConfig.from_config(event_config),
            postprocessor=partial(event_postprocessor, 
                                has_next_indicator='', 
                                current_number=new_node.number().strip()),
            filter=wrap_filter_for_tuple(
                min_max_tokens_filter(0, event_config['max_tokens']) + 
                word_filter(['[', 'TODO', ']', ':'])
            ),
        )
        
        if not event_result:
            raise ValueError("Failed to generate event text")
        new_node.text = event_result[0][0]  # Get the first event's text
        
        generate_node_scene(new_node, llm_client, prompts['scene'], config['scene'], plan)
        generate_node_entities(new_node, llm_client, prompts['entity'], config['entity'], plan)
        
        try:
            generate_node_subevents(new_node, llm_client, prompts, config, plan)
        except Exception as e:
            logging.error(f"Failed in generate_node_subevents: {str(e)}")
            plan.outline.children.remove(new_node)
            return plots

        logging.info(f"extend_event_depth_0, new_node text: {new_node.text}, scene: {new_node.scene}, entities: {getattr(new_node, 'entities', [])}")
        for i, child in enumerate(new_node.children, 1):
            logging.info(f"Child {i}:")
            logging.info(f"  - Content: {child.text}")
            logging.info(f"  - Scene: {getattr(child, 'scene', 'No scene')}")
            logging.info(f"  - Entities: {getattr(child, 'entities', [])}")
            if child.text:
                plots.append(child.text)
        # Note: do not store the new node, just return plots!
        plan.outline.children.remove(new_node)
        return plots
        
    except Exception as e:
        logging.error(f"Failed to extend outline: {str(e)}")
        logging.exception("Full stack trace:")
        if new_node in plan.outline.children:
            plan.outline.children.remove(new_node)
        return plots

def plan_extend_by_plot(plan, llm_client, prompts, config, plot):
    if not plan.outline:
        raise ValueError("Plan outline is empty")
    
    new_node = OutlineNode('', plan.outline)
    new_node.text = plot
    plan.outline.children.append(new_node)

    try:
        generate_node_scene(new_node, llm_client, prompts['scene'], config['scene'], plan)
        generate_node_entities(new_node, llm_client, prompts['entity'], config['entity'], plan)
        generate_node_subevents(new_node, llm_client, prompts, config, plan)

        logging.info(f"extend new_node text: {new_node.text}, scene: {new_node.scene}, entities: {getattr(new_node, 'entities', [])}")
        for i, child in enumerate(new_node.children, 1):
            logging.info(f"Child {i}:")
            logging.info(f"  - Content: {child.text}")
            logging.info(f"  - Scene: {getattr(child, 'scene', 'No scene')}")
            logging.info(f"  - Entities: {getattr(child, 'entities', [])}")
        
    except Exception as e:
        logging.error(f"Failed to extend outline: {str(e)}")
        if new_node in plan.outline.children:
            plan.outline.children.remove(new_node)
        return None

    return plan