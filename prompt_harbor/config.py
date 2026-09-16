import os

DEFAULT_DB = 'gateway.db'
DEFAULT_LISTEN = '127.0.0.1:8787'
DEFAULT_UPSTREAM = 'https://api.openai.com'
DEFAULT_MAX_BODY = 10 * 1024 * 1024

def value(cli, env_name, default):
    return cli or os.getenv(env_name) or default

