# Flask Proxy Server

A robust Flask-based HTTP proxy server that routes requests through random proxies from a public proxy list.

## Features

- **Automatic Proxy Rotation**: Randomly selects from available HTTP proxies for each request
- **Retry Logic**: Automatically tries up to 5 different proxies if one fails
- **Proxy Filtering**: Filters for HTTP-only proxies (avoiding SOCKS proxies that need special handling)
- **Quality Sorting**: Prioritizes proxies with high uptime, speed, and low latency
- **Caching**: Caches proxy list for 5 minutes to reduce API calls
- **Header & Body Forwarding**: Forwards all headers and request body
- **All HTTP Methods**: Supports GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
- **Health Check**: `/health` endpoint to check server status and available proxies

## Installation

```bash
pip install flask requests urllib3
```

## Usage

### Start the Server

```bash
python shore.py
```

Server runs on `http://localhost:5000`

### Make Requests

Forward requests to any site by using the path `/<site>`:

```bash
# Simple GET request
curl http://localhost:5000/example.com

# GET with full URL
curl http://localhost:5000/https://api.example.com/data

# POST with JSON body
curl -X POST http://localhost:5000/httpbin.org/post \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}'

# Custom headers
curl http://localhost:5000/httpbin.org/headers \
  -H "X-Custom-Header: test"

# Check server health
curl http://localhost:5000/health
```

### Run Tests

```bash
python test_proxy.py
```

The test suite includes:
- Health check
- GET/POST/PUT/DELETE requests
- Header forwarding
- Query parameter forwarding
- Multiple sequential requests

## How It Works

1. **Proxy Fetching**: Retrieves proxy list from `proxylist.geonode.com`
2. **Filtering**: Keeps only HTTP proxies (filters out SOCKS)
3. **Sorting**: Orders by uptime, speed, and latency
4. **Caching**: Stores filtered list for 5 minutes
5. **Retry Logic**: On each request:
   - Selects a random proxy from filtered list
   - Attempts connection with 30s timeout
   - If fails, tries different proxy (up to 5 attempts)
   - Returns response from first successful proxy

## Configuration

Edit `shore.py` to customize:

```python
# Cache TTL (seconds)
proxy_cache['ttl'] = 300  # Default: 5 minutes

# Max retry attempts
MAX_RETRIES = 5  # Default: 5 attempts

# Request timeout
timeout=30  # Default: 30 seconds

# Proxy endpoint
endpoint = "https://proxylist.geonode.com/api/proxy-list?limit=500"
```

## Endpoints

### `/<path:site>`
Proxy endpoint that forwards requests to the specified site.

**Methods**: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS

**Examples**:
- `/example.com` → `https://example.com`
- `/api.github.com/users` → `https://api.github.com/users`

### `/health`
Health check endpoint.

**Method**: GET

**Response**:
```json
{
  "status": "ok",
  "proxies_available": 145,
  "cache_age_seconds": 42
}
```

## Notes

- Free proxies can be unreliable; retry logic helps mitigate this
- SSL verification is disabled (`verify=False`) due to proxy SSL issues
- The proxy list updates every 5 minutes automatically
- Some requests may take longer due to proxy latency
- Success rate depends on proxy quality from the endpoint

## Troubleshooting

**All requests failing (502 errors)**:
- Check if proxy endpoint is accessible
- Try increasing `MAX_RETRIES`
- Check `/health` to see available proxies

**Slow requests**:
- Adjust timeout value
- Free proxies can be slow
- Consider implementing proxy health checks

**SOCKS proxy errors**:
- Current version filters for HTTP-only proxies
- If you need SOCKS support, install: `pip install requests[socks]`
