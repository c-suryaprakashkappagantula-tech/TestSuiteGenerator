"""
llm_engine.py — Abstracted LLM client for TestSuiteGenerator V4.
Supports: OpenAI, Azure OpenAI, AWS Bedrock (Claude), and Ollama (local).
Falls back gracefully if no LLM is configured — engine still works rule-based.
"""
import os
import json
import time

# Provider constants
PROVIDER_OPENAI = 'openai'
PROVIDER_AZURE = 'azure'
PROVIDER_BEDROCK = 'bedrock'
PROVIDER_OLLAMA = 'ollama'
PROVIDER_NONE = 'none'

# Default models per provider
DEFAULT_MODELS = {
    PROVIDER_OPENAI: 'gpt-4o',
    PROVIDER_AZURE: 'gpt-4o',
    PROVIDER_BEDROCK: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
    PROVIDER_OLLAMA: 'llama3.1',
}

_MAX_RETRIES = 2
_RETRY_DELAY = 2  # seconds


class LLMClient:
    """Unified LLM client. Construct with provider + config, call .chat() to get responses."""

    def __init__(self, provider=PROVIDER_NONE, model=None, api_key=None,
                 base_url=None, azure_endpoint=None, azure_deployment=None,
                 azure_api_version='2024-06-01', region='us-east-1',
                 temperature=0.3, max_tokens=4096, log=print):
        self.provider = provider.lower().strip() if provider else PROVIDER_NONE
        self.model = model or DEFAULT_MODELS.get(self.provider, '')
        self.api_key = api_key or ''
        self.base_url = base_url or ''
        self.azure_endpoint = azure_endpoint or ''
        self.azure_deployment = azure_deployment or ''
        self.azure_api_version = azure_api_version
        self.region = region
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.log = log
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self):
        """Initialize the underlying client based on provider."""
        if self.provider == PROVIDER_NONE:
            self.log('[LLM] No provider configured — running in rule-based mode')
            return

        try:
            if self.provider == PROVIDER_OPENAI:
                from openai import OpenAI
                kwargs = {'api_key': self.api_key}
                if self.base_url:
                    kwargs['base_url'] = self.base_url
                self._client = OpenAI(**kwargs)
                self._available = True
                self.log('[LLM] OpenAI client ready (model=%s)' % self.model)

            elif self.provider == PROVIDER_AZURE:
                from openai import AzureOpenAI
                self._client = AzureOpenAI(
                    api_key=self.api_key,
                    azure_endpoint=self.azure_endpoint,
                    api_version=self.azure_api_version,
                )
                self._available = True
                self.log('[LLM] Azure OpenAI client ready (deployment=%s)' % self.azure_deployment)

            elif self.provider == PROVIDER_BEDROCK:
                import boto3
                self._client = boto3.client('bedrock-runtime', region_name=self.region)
                self._available = True
                self.log('[LLM] AWS Bedrock client ready (model=%s, region=%s)' % (self.model, self.region))

            elif self.provider == PROVIDER_OLLAMA:
                # Ollama uses OpenAI-compatible API
                from openai import OpenAI
                url = self.base_url or 'http://localhost:11434/v1'
                self._client = OpenAI(base_url=url, api_key='ollama')
                self._available = True
                self.log('[LLM] Ollama client ready (model=%s, url=%s)' % (self.model, url))

            else:
                self.log('[LLM] Unknown provider: %s' % self.provider)

        except ImportError as e:
            self.log('[LLM] Missing dependency: %s — install with pip' % e)
        except Exception as e:
            self.log('[LLM] Init failed: %s' % e)

    @property
    def available(self):
        return self._available

    def chat(self, system_prompt, user_prompt, temperature=None, max_tokens=None):
        """Send a chat completion request. Returns the response text or empty string on failure."""
        if not self._available:
            return ''

        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if self.provider in (PROVIDER_OPENAI, PROVIDER_OLLAMA):
                    return self._chat_openai(system_prompt, user_prompt, temp, tokens)
                elif self.provider == PROVIDER_AZURE:
                    return self._chat_azure(system_prompt, user_prompt, temp, tokens)
                elif self.provider == PROVIDER_BEDROCK:
                    return self._chat_bedrock(system_prompt, user_prompt, temp, tokens)
            except Exception as e:
                self.log('[LLM] Attempt %d/%d failed: %s' % (attempt, _MAX_RETRIES, e))
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
        return ''

    def _chat_openai(self, system_prompt, user_prompt, temp, tokens):
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=temp,
            max_tokens=tokens,
        )
        return resp.choices[0].message.content.strip()

    def _chat_azure(self, system_prompt, user_prompt, temp, tokens):
        resp = self._client.chat.completions.create(
            model=self.azure_deployment,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=temp,
            max_tokens=tokens,
        )
        return resp.choices[0].message.content.strip()

    def _chat_bedrock(self, system_prompt, user_prompt, temp, tokens):
        body = json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': tokens,
            'temperature': temp,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_prompt}],
        })
        resp = self._client.invoke_model(modelId=self.model, body=body, contentType='application/json')
        result = json.loads(resp['body'].read())
        return result['content'][0]['text'].strip()


def create_llm_from_env(log=print):
    """Auto-detect LLM provider from environment variables.
    Checks: OPENAI_API_KEY, AZURE_OPENAI_*, AWS (boto3 creds), OLLAMA_HOST."""
    # OpenAI
    if os.environ.get('OPENAI_API_KEY'):
        return LLMClient(
            provider=PROVIDER_OPENAI,
            api_key=os.environ['OPENAI_API_KEY'],
            model=os.environ.get('OPENAI_MODEL', DEFAULT_MODELS[PROVIDER_OPENAI]),
            base_url=os.environ.get('OPENAI_BASE_URL', ''),
            log=log,
        )
    # Azure OpenAI
    if os.environ.get('AZURE_OPENAI_API_KEY') and os.environ.get('AZURE_OPENAI_ENDPOINT'):
        return LLMClient(
            provider=PROVIDER_AZURE,
            api_key=os.environ['AZURE_OPENAI_API_KEY'],
            azure_endpoint=os.environ['AZURE_OPENAI_ENDPOINT'],
            azure_deployment=os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o'),
            azure_api_version=os.environ.get('AZURE_OPENAI_API_VERSION', '2024-06-01'),
            model=os.environ.get('AZURE_OPENAI_MODEL', DEFAULT_MODELS[PROVIDER_AZURE]),
            log=log,
        )
    # Ollama (check if running locally)
    ollama_host = os.environ.get('OLLAMA_HOST', '')
    if ollama_host:
        return LLMClient(
            provider=PROVIDER_OLLAMA,
            base_url=ollama_host.rstrip('/') + '/v1',
            model=os.environ.get('OLLAMA_MODEL', DEFAULT_MODELS[PROVIDER_OLLAMA]),
            log=log,
        )
    # AWS Bedrock (check boto3 credentials)
    try:
        import boto3
        sts = boto3.client('sts')
        sts.get_caller_identity()
        return LLMClient(
            provider=PROVIDER_BEDROCK,
            region=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
            model=os.environ.get('BEDROCK_MODEL', DEFAULT_MODELS[PROVIDER_BEDROCK]),
            log=log,
        )
    except Exception:
        pass

    return LLMClient(provider=PROVIDER_NONE, log=log)
