import os, json, logging
import openai
from storygenv1.common.util import *

class SamplingConfig:
    def __init__(self, 
                 server_config,
                 prompt_format,
                 max_tokens=None,
                 temperature=None,
                 top_p=None,
                 frequency_penalty=None,
                 presence_penalty=None,
                 stop=None,
                 n=None,
                 logit_bias=None,
                 logprobs=None):
        self.server_config = server_config
        self.prompt_format = prompt_format
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.stop = stop
        self.n = n
        self.logit_bias = logit_bias
        self.logprobs = logprobs
    
    @staticmethod
    def from_config(config):
        return SamplingConfig(
            server_config=ServerConfig.from_config(config),
            prompt_format=config['prompt_format'],
            max_tokens=config.get('max_tokens', None),
            temperature=config.get('temperature', None),
            top_p=config.get('top_p', None),
            frequency_penalty=config.get('frequency_penalty', None),
            presence_penalty=config.get('presence_penalty', None),
            stop=config.get('stop', None),
            n=config.get('n', None),
            logit_bias=config.get('logit_bias', None),
            logprobs=config.get('logprobs', None)
        )
    
    def __getitem__(self, key):
        return getattr(self, key)
    
    def dict(self):
        d = {'model': self.server_config.engine}
        for attr in ['max_tokens', 'temperature', 'top_p', 'frequency_penalty', 'presence_penalty', 'stop', 'n', 'logit_bias', 'logprobs']:
            if getattr(self, attr) is not None:
                d[attr] = getattr(self, attr)
        return d



class ServerConfig:
    def __init__(self, engine, host, port, server_type):
        self.engine = engine
        self.host = host
        self.port = port
        self.server_type = server_type

    @staticmethod
    def from_config(config):
        return ServerConfig(
            engine=config['engine'],
            host=config['host'],
            port=config.get('port'),
            server_type=config['server_type'],
        )

    @staticmethod
    def from_json(json_str):
        return ServerConfig(**json.loads(json_str))

    def json(self):
        return json.dumps({
            'engine': self.engine,
            'host': self.host,
            'port': self.port,
            'server_type': self.server_type,
        })

    def __getitem__(self, key):
        return getattr(self, key)

    def __hash__(self):
        return hash((self.engine, self.host, self.port,
                     self.server_type))

    def __eq__(self, other):
        return (self.engine, self.host, self.port, self.server_type) == (other.engine, other.host, other.port, other.server_type)


class LLMClient:
    def __init__(self):
        self.warned = {'vllm_logit_bias': False}
        #openai.debug = True

    def call_with_retry(self, prompt_builder, sampling_config, postprocessor=None, filter=lambda s: len(s.strip()) > 0, max_attempts=5, **kwargs):
        for attempt in range(max_attempts):
            try:
                completions, full_completion_object = self(prompt_builder, sampling_config, **kwargs)
                if postprocessor is not None:
                    completions = postprocessor(completions, full_completion_object=full_completion_object)
                
                completions = [c for c in completions if filter(c)]

                if len(completions) > 0 or kwargs.get('empty_ok', False):
                    if kwargs.get('return_full_completion', False):
                        return completions, full_completion_object
                    else:
                        return completions
            except Exception as e:
                logging.error(f"Error in call_with_retry: {str(e)}")
                if attempt < max_attempts - 1:
                    logging.info(f"Retrying... ({attempt + 2}/{max_attempts})")
                continue
            
        error_msg = f"Failed to get a valid completion after {max_attempts} attempts."
        logging.error(error_msg)
        #raise RuntimeError(error_msg)
    
    def __call__(self, prompt_builder, sampling_config, **kwargs):
        if sampling_config.server_config['server_type'] == 'openai':
            openai.api_key = os.environ['OPENAI_API_KEY']
            openai.api_base = 'https://api.openai.com/v1'
        elif sampling_config.server_config['server_type'] == 'vllm':
            openai.api_key = "EMPTY"
            openai.api_base = sampling_config.server_config['host'] + ':' + str(sampling_config.server_config['port']) + '/v1'
            if 'logit_bias' in sampling_config.dict():
                if not self.warned['vllm_logit_bias']:
                    logging.warning(f"Logit bias is not supported for vllm server.")
                    self.warned['vllm_logit_bias'] = True
        elif sampling_config.server_config['server_type'] == 'together-chat':
            openai.api_key = os.environ.get('TOGETHER_API_KEY')
            openai.api_base = 'https://api.together.xyz/v1'   
        else:
            raise NotImplementedError(f"Engine type {self.sampling_config.server_config['server_type']} not implemented.")
        
        prompt = prompt_builder.render_for_llm_format(sampling_config.prompt_format)

        if sampling_config['prompt_format'] in ['openai-chat', 'together-chat']:
            completion = openai.ChatCompletion.create(messages=prompt, timeout=60, **sampling_config.dict())
            texts = [c.message['content'] for c in completion.choices]
            
            # strip response prefix
            if prompt_builder.response_prefix is not None:
                for i, text in enumerate(texts):
                    if text.startswith(prompt_builder.response_prefix.format()):
                        texts[i] = text[len(prompt_builder.response_prefix.format()):]
        else:
            # llama2-chat
            params = sampling_config.dict()
            if 'logit_bias' in params:
                del params['logit_bias'] # vllm doesn't yet support logit bias
            completion = openai.Completion.create(prompt=prompt, **params) 
            texts = [c.text for c in completion.choices]
        
        if prompt_builder.output_prefix is not None:
            for i, text in enumerate(texts):
                texts[i] = prompt_builder.output_prefix.rstrip() + ' ' + text.lstrip()
        return texts, completion