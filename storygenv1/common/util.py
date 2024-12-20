from contextlib import contextmanager
import logging
import re
import signal

import roman
import Levenshtein
from transformers import AutoTokenizer
from scipy.special import log_softmax

from storygen.common.llm.prompt import TemplatePromptBuilder

tokenizers = {}

def init_logging(logging_level):
    logging_level = logging_level.upper()
    assert logging_level in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s',
                        level=logging_level,
                        datefmt='%Y-%m-%d %H:%M:%S')


class TimeoutException(Exception): pass

# @contextmanager
# def time_limit(seconds):
#     def signal_handler(signum, frame):
#         raise TimeoutException("Timed out!")
#     signal.signal(signal.SIGALRM, signal_handler)
#     signal.alarm(seconds)
#     try:
#         yield
#     finally:
#         signal.alarm(0)


def num_to_char(num, newline=False):
    if num > 26:
        return num_to_char(num // 26) + num_to_char(num % 26)
    new_char = chr(num-1 + ord('a')) # 1:a 2:b etc
    if newline:
        return '\n' + new_char
    else:
        return new_char


def num_to_roman(num, newline=False):
    new_num = roman.toRoman(num)
    if newline:
        return '\n' + new_num.lower()
    else:
        return new_num.lower()


class Filter:
    def __init__(self, filter_func):
        self.filter_func = filter_func

    @staticmethod
    def wrap_preprocessor(preprocessor, filter):
        return Filter(lambda s: filter(preprocessor(s)))
    
    def __call__(self, *args, **kwargs):
        try:
            return self.filter_func(*args, **kwargs)
        except:
            return self.filter_func(*args) # for any functions that don't take extra kwargs

    def __add__(self, other):
        return Filter(lambda s: self.filter_func(s) and other.filter_func(s))


def min_max_tokens_filter(min_tokens, max_tokens, tokenizer_model_string='gpt2', filter_empty=True):
    # the tokenizer model doesn't really matter. we're just counting tokens for filtering purposes
    global tokenizers
    if tokenizer_model_string in tokenizers:
        tokenizer = tokenizers[tokenizer_model_string]
    else:
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_model_string)
        tokenizers[tokenizer_model_string] = tokenizer
    filter = Filter(lambda s: min_tokens <= len(tokenizer.encode(s.strip())) <= max_tokens)
    if filter_empty:
        filter = filter + Filter(lambda s: len(s.strip()) > 0)
    return filter


def levenshtein_ratio_filter(passages_to_match, threshold=0.8):
    # check if any subpassage of the generated passage are too similar to any passage in passages_to_match
    return Filter(lambda s: all([all([Levenshtein.ratio(sub_s, passage) < threshold for passage in passages_to_match]) for sub_s in s.split()]))


def word_filter(word_list):
    return Filter(lambda s: all([word not in s for word in word_list]))


def list_next_number_format_filter():
    # check if any list numbering e.g. "4." is preceded by a newline
    bad_regex = re.compile(r'[^\n]\d+\.')
    return Filter(lambda s: not bad_regex.search(s))


def wrap_filter_for_tuple(filter, index=0):
    return Filter(lambda s: filter(s[index]))

# NOTE: only works with together-chat
def extract_choice_logprobs(full_completion, choices=['yes', 'no'], default_logprobs=[-20, -20], case_sensitive=False):
    batch_logprobs = []
    
    for choice in full_completion['choices']:
        if 'logprobs' not in choice:
            logging.warning("No logprobs in choice")
            batch_logprobs.append(default_logprobs)
            continue
            
        tokens = choice['logprobs']['tokens']
        token_logprobs = choice['logprobs']['token_logprobs']
        
        logprobs = list(default_logprobs)
        found = False
        for token, logprob in zip(tokens, token_logprobs):
            for i, choice_text in enumerate(choices):
                if (case_sensitive and choice_text == token) or \
                   (not case_sensitive and choice_text.lower() == token.lower()):
                    logprobs[i] = float(logprob)
                    found = True
                    for j in range(len(choices)):
                        if j != i:
                            logprobs[j] = float(logprob) - 10.0
                    break
            if found:
                break
                    
        if not found:
            logging.warning(f"No matching tokens found for choices {choices}")
            logprobs = list(default_logprobs)
        
        batch_logprobs.append(logprobs)
        
    return batch_logprobs
