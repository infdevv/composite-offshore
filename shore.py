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

def fetch_proxies_from_endpoints():
    all_proxies = []
    for endpoint in PROXY_ENDPOINTS:
        try:
            response = requests.get(endpoint, timeout=10)
            if response.status_code == 200:
                data = response.json()
                proxies = data.get('data', [])
                all_proxies.extend(proxies)
            else:
                pass
        except Exception as e:
            pass
    return all_proxies

def get_proxies():
    current_time = datetime.now()

    if (proxy_cache['data'] and
        proxy_cache['timestamp'] and
        (current_time - proxy_cache['timestamp']).seconds < proxy_cache['ttl']):
        print(f"Using cached proxies ({len(proxy_cache['data'])} proxies)")
        return proxy_cache['data']

    print("Fetching fresh proxies...")
    all_proxies = fetch_proxies_from_endpoints()

    http_proxies = [p for p in all_proxies if 'http' in p.get('protocols', []) or 'https' in p.get('protocols', [])]

    proxy_cache['data'] = http_proxies
    proxy_cache['timestamp'] = current_time

    print(f"Fetched {len(http_proxies)} proxies")
    return http_proxies

def select_random_proxy(proxies, exclude_indices=None):
    """Select a random working proxy"""
    if not proxies:
        return None, None

    if exclude_indices is None:
        exclude_indices = set()

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

@app.route('/health', methods=['GET'])
def health():
    proxies_list = get_proxies()
    return flask.jsonify({
        'status': 'ok',
        'proxies_available': len(proxies_list),
        'cache_age_seconds': (datetime.now() - proxy_cache['timestamp']).seconds if proxy_cache['timestamp'] else None
    })

@app.route('/<path:site>', methods=['GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS'])
def proxy(site):
    if flask.request.method == 'OPTIONS':
        response = flask.make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        return response
    MAX_RETRIES = 3
    tried_indices = set()
    last_error = None

    proxies_list = get_proxies()

    target_url = site
    if not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url

    headers = {key: value for key, value in flask.request.headers if key.lower() != 'host'}
    method = flask.request.method
    data = flask.request.get_data()
    params = flask.request.args

    def stream_response(resp):
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    for attempt in range(MAX_RETRIES):
        if proxies_list:
            proxy_data, proxy_index = select_random_proxy(proxies_list, tried_indices)
            if proxy_data is None:
                continue
            tried_indices.add(proxy_index)
            proxy_url = format_proxy_url(proxy_data)

            try:
                proxies = {'http': proxy_url, 'https': proxy_url}

                response = requests.request(
                    method=method,
                    url=target_url,
                    headers=headers,
                    data=data,
                    params=params,
                    proxies=proxies,
                    allow_redirects=True,
                    timeout=30,
                    verify=False,
                    stream=True  # <--- enable streaming
                )

                excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
                response_headers = [
                    (name, value) for name, value in response.raw.headers.items()
                    if name.lower() not in excluded_headers
                ]

                return flask.Response(
                    stream_response(response),
                    status=response.status_code,
                    headers=response_headers,
                    proxy = proxy_url
                )

            except Exception as e:
                last_error = e
                continue

    # If all proxies fail, try direct connection
    try:
        response = requests.request(
            method=method,
            url=target_url,
            headers=headers,
            data=data,
            params=params,
            allow_redirects=True,
            timeout=30,
            stream=True
        )

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = [
            (name, value) for name, value in response.raw.headers.items()
            if name.lower() not in excluded_headers
        ]

        return flask.Response(
            stream_response(response),
            status=response.status_code,
            headers=response_headers
        )

    except Exception as e:
        return flask.jsonify({
            'error': 'All proxy attempts failed and direct connection failed',
            'last_error': str(last_error) if last_error else str(e)
        }), 502


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)