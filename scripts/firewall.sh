#!/usr/bin/env bash
set -euo pipefail
ufw allow from 192.168.50.0/24 to any port 3401 proto tcp comment 'Chengtin local media transcription'
ufw status numbered
