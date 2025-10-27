import flask
import requests
import json
import random
import sys
import time
import urllib3
from datetime import datetime, timedelta
from flask_cors import CORS

sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROXY_ENDPOINTS = [
    "https://proxylist.geonode.com/api/proxy-list?anonymityLevel=elite&filterUpTime=90&speed=fast&google=false&limit=500&page=1&sort_by=lastChecked&sort_type=desc",
]

app = flask.Flask(__name__)
CORS(app)

proxy_cache = {
    'data': [],
    'timestamp': None,
    'ttl': 5 * 60
}

# Headers that should NOT be forwarded to target
BLOCKED_REQUEST_HEADERS = {
    'host', 'origin', 'referer', 'x-forwarded-for', 
    'x-forwarded-proto', 'x-forwarded-host', 'x-real-ip',
    'connection', 'accept-encoding'
}

# Headers to remove from response before sending back
BLOCKED_RESPONSE_HEADERS = {
    'content-encoding', 'content-length', 'transfer-encoding', 
    'connection', 'access-control-allow-origin', 
    'access-control-allow-credentials', 'access-control-expose-headers',
    'access-control-allow-methods', 'access-control-allow-headers'
}

def fetch_proxies_from_endpoints():
    all_proxies = []
    for endpoint in PROXY_ENDPOINTS:
        try:
            response = requests.get(endpoint, timeout=10)
            if response.status_code == 200:
                data = response.json()
                proxies = data.get('data', [])
                all_proxies.extend(proxies)
        except Exception:
            pass
    return all_proxies

def get_proxies():
    current_time = datetime.now()

    if (proxy_cache['data'] and 
        proxy_cache['timestamp'] and
        (current_time - proxy_cache['timestamp']).total_seconds() < proxy_cache['ttl']):
        print(f"Using cached proxies ({len(proxy_cache['data'])} proxies)")
        return proxy_cache['data']

    print("Fetching fresh proxies...")
    all_proxies = fetch_proxies_from_endpoints()
    http_proxies = [
        p for p in all_proxies 
        if 'http' in p.get('protocols', []) or 'https' in p.get('protocols', [])
    ]

    proxy_cache['data'] = http_proxies
    proxy_cache['timestamp'] = current_time

    print(f"Fetched {len(http_proxies)} proxies")
    return http_proxies

def select_random_proxy(proxies, exclude_indices=None):
    if not proxies:
        return None, None

    exclude_indices = exclude_indices or set()
    available_indices = [i for i in range(len(proxies)) if i not in exclude_indices]

    if not available_indices:
        return None, None

    index = random.choice(available_indices)
    return proxies[index], index

def format_proxy_url(proxy_data):
    ip = proxy_data['ip']
    port = proxy_data['port']
    protocol = proxy_data.get('protocols', ['http'])[0].lower()
    return f"{protocol}://{ip}:{port}"

def build_request_headers(original_headers):
    """Filter out problematic headers that shouldn't be proxied"""
    return {
        key: value 
        for key, value in original_headers 
        if key.lower() not in BLOCKED_REQUEST_HEADERS
    }

def build_response_headers(response_headers):
    """
    Filter response headers and add CORS headers for browser compatibility
    """
    filtered = [
        (name, value) 
        for name, value in response_headers.items()
        if name.lower() not in BLOCKED_RESPONSE_HEADERS
    ]
    
    # Add permissive CORS headers
    cors_headers = [
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS'),
        ('Access-Control-Allow-Headers', '*'),
        ('Access-Control-Expose-Headers', '*'),
    ]
    
    return filtered + cors_headers

@app.route('/health', methods=['GET'])
def health():
    proxies_list = get_proxies()
    cache_age = None
    if proxy_cache['timestamp']:
        cache_age = (datetime.now() - proxy_cache['timestamp']).total_seconds()
    
    return flask.jsonify({
        'status': 'ok',
        'proxies_available': len(proxies_list),
        'cache_age_seconds': cache_age
    })

def handle_chat_completions(site, use_proxy=True):
    """
    Shared logic for chat completions endpoints.
    Returns a Flask Response or tuple (response, status_code).
    """
    # Build target URL - append /v1/chat/completions to the base site
    base_url = site if site.startswith(('http://', 'https://')) else f'https://{site}'
    base_url = base_url.rstrip('/')
    target_url = f'{base_url}/v1/chat/completions'

    # Get OpenAI-style headers and payload
    headers = build_request_headers(flask.request.headers)
    
    # Ensure Content-Type is set for JSON
    if 'content-type' not in {k.lower() for k in headers.keys()}:
        headers['Content-Type'] = 'application/json'

    try:
        payload = flask.request.get_json(force=True)
    except Exception:
        return flask.jsonify({
            'error': 'Invalid JSON payload',
            'message': 'Expected OpenAI-compatible JSON payload'
        }), 400

    # Check if streaming is requested
    is_streaming = payload.get('stream', False)

    if use_proxy:
        MAX_RETRIES = 3
        tried_indices = set()
        last_error = None
        proxies_list = get_proxies()

        def make_request_with_proxy():
            proxy_data, proxy_index = select_random_proxy(proxies_list, tried_indices)
            if not proxy_data:
                return None
            
            tried_indices.add(proxy_index)
            proxy_url = format_proxy_url(proxy_data)
            print(f"Trying proxy: {proxy_url}")
            
            return requests.post(
                url=target_url,
                headers=headers,
                json=payload,
                proxies={'http': proxy_url, 'https': proxy_url},
                allow_redirects=True,
                timeout=120,
                verify=False,
                stream=is_streaming
            )

        # Try with proxies first
        for attempt in range(MAX_RETRIES):
            if not proxies_list or len(tried_indices) >= len(proxies_list):
                break
                
            try:
                response = make_request_with_proxy()
                if response is None:
                    break
                    
                response_headers = build_response_headers(response.raw.headers)
                
                if is_streaming:
                    def generate():
                        for chunk in response.iter_content(chunk_size=None, decode_unicode=False):
                            if chunk:
                                yield chunk
                    
                    return flask.Response(
                        generate(),
                        status=response.status_code,
                        headers=response_headers,
                        mimetype='text/event-stream'
                    )
                else:
                    return flask.Response(
                        response.content,
                        status=response.status_code,
                        headers=response_headers
                    )
            except Exception as e:
                last_error = e
                print(f"Proxy attempt {attempt + 1} failed: {e}")
                continue

        # Fallback to direct connection
        print("All proxies failed, trying direct connection...")
        try:
            response = requests.post(
                url=target_url,
                headers=headers,
                json=payload,
                allow_redirects=True,
                timeout=120,
                verify=False,
                stream=is_streaming
            )
            
            response_headers = build_response_headers(response.raw.headers)
            
            if is_streaming:
                def generate():
                    for chunk in response.iter_content(chunk_size=None, decode_unicode=False):
                        if chunk:
                            yield chunk
                
                return flask.Response(
                    generate(),
                    status=response.status_code,
                    headers=response_headers,
                    mimetype='text/event-stream'
                )
            else:
                return flask.Response(
                    response.content,
                    status=response.status_code,
                    headers=response_headers
                )
        except Exception as e:
            return flask.jsonify({
                'error': {
                    'message': 'All proxy attempts failed and direct connection failed',
                    'type': 'proxy_error',
                    'last_error': str(last_error) if last_error else str(e),
                    'target_url': target_url
                }
            }), 502
    else:
        # Direct connection without proxy
        try:
            response = requests.post(
                url=target_url,
                headers=headers,
                json=payload,
                allow_redirects=True,
                timeout=120,
                verify=False,
                stream=is_streaming
            )
            
            response_headers = build_response_headers(response.raw.headers)
            
            if is_streaming:
                def generate():
                    for chunk in response.iter_content(chunk_size=None, decode_unicode=False):
                        if chunk:
                            yield chunk
                
                return flask.Response(
                    generate(),
                    status=response.status_code,
                    headers=response_headers,
                    mimetype='text/event-stream'
                )
            else:
                return flask.Response(
                    response.content,
                    status=response.status_code,
                    headers=response_headers
                )
        except Exception as e:
            return flask.jsonify({
                'error': {
                    'message': 'Direct connection failed',
                    'type': 'connection_error',
                    'error': str(e),
                    'target_url': target_url
                }
            }), 502

@app.route('/v1/chat/completions/<path:site>', methods=['POST', 'OPTIONS'])
def chat_completions(site):
    """
    OpenAI-compatible endpoint that proxies to arbitrary base URLs.
    Accepts OpenAI headers/payload and forwards to {site}/v1/chat/completions
    """
    if flask.request.method == 'OPTIONS':
        response = flask.make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response

    return handle_chat_completions(site, use_proxy=True)

@app.route('/v1/chat/completions/noproxy/<path:site>', methods=['POST', 'OPTIONS'])
def chat_completions_noproxy(site):
    """
    OpenAI-compatible endpoint with direct connection (no proxy).
    Useful for OpenAI module usage when proxies cause issues.
    Accepts OpenAI headers/payload and forwards to {site}/v1/chat/completions
    """
    if flask.request.method == 'OPTIONS':
        response = flask.make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response

    return handle_chat_completions(site, use_proxy=False)

@app.route('/<path:site>', methods=['GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy(site):
    """General purpose proxy for any HTTP method to any URL"""
    # Handle preflight OPTIONS requests immediately
    if flask.request.method == 'OPTIONS':
        response = flask.make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response

    MAX_RETRIES = 3
    tried_indices = set()
    last_error = None

    proxies_list = get_proxies()

    target_url = site if site.startswith(('http://', 'https://')) else f'https://{site}'
    headers = build_request_headers(flask.request.headers)
    method = flask.request.method
    data = flask.request.get_data()
    params = flask.request.args

    def make_request(use_proxy=True):
        kwargs = {
            'method': method,
            'url': target_url,
            'headers': headers,
            'data': data,
            'params': params,
            'allow_redirects': True,
            'timeout': 30,
            'verify': False,
            'stream': True
        }
        
        if use_proxy and proxies_list:
            proxy_data, proxy_index = select_random_proxy(proxies_list, tried_indices)
            if proxy_data:
                tried_indices.add(proxy_index)
                proxy_url = format_proxy_url(proxy_data)
                kwargs['proxies'] = {'http': proxy_url, 'https': proxy_url}
                print(f"Trying proxy: {proxy_url}")
        
        return requests.request(**kwargs)

    # Try with proxies first
    for attempt in range(MAX_RETRIES):
        if not proxies_list or len(tried_indices) >= len(proxies_list):
            break
            
        try:
            response = make_request(use_proxy=True)
            response_headers = build_response_headers(response.raw.headers)
            
            return flask.Response(
                response.iter_content(chunk_size=8192),
                status=response.status_code,
                headers=response_headers
            )
        except Exception as e:
            last_error = e
            print(f"Proxy attempt {attempt + 1} failed: {e}")
            continue

    # Fallback to direct connection
    print("All proxies failed, trying direct connection...")
    try:
        response = make_request(use_proxy=False)
        response_headers = build_response_headers(response.raw.headers)
        
        return flask.Response(
            response.iter_content(chunk_size=8192),
            status=response.status_code,
            headers=response_headers
        )
    except Exception as e:
        return flask.jsonify({
            'error': 'All proxy attempts failed and direct connection failed',
            'last_error': str(last_error) if last_error else str(e),
            'target_url': target_url
        }), 502


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)