#!/bin/bash

url="${1}"
key="${2}"
lat="${3}"
lon="${4}"
alt="${5}"

datestamp="$(date +%s)"
datestr="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

curl -X POST "${url}/api/submit" \
  -H 'User-Agent: fake' \
  -H "Authorization: Bearer ${key}" \
  -H "Content-Type: application/json" \
  -d '[{"uas_id": "TEST-'${datestamp}'", "timestamp": "'${datestr}'", "latitude": '${lat}', "longitude": '${lon}', "altitude": '${alt}', "height": 50, "height_type": "agl"}]'
