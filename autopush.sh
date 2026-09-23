#!/bin/zsh
cd ~/hackalem || exit 1
echo "autopush запущен (каждые 5 мин). Ctrl+C чтобы остановить."
while true; do
  git add -A
  if ! git diff --cached --quiet; then
    git commit -m "auto: $(date '+%Y-%m-%d %H:%M:%S')" >/dev/null
    git push && echo "[$(date '+%H:%M:%S')] залито ✅"
  fi
  sleep 300
done
