#!/bin/sh
set -eu

if [ ! -f backend/.env.production ]; then
  cp backend/.env.prod.example backend/.env.production
  echo "backend/.env.production yaratildi. CHANGE_ME qiymatlarini to'ldiring."
  exit 1
fi
if [ ! -f .env.mysql ]; then
  cp .env.mysql.example .env.mysql
  echo ".env.mysql yaratildi. CHANGE_ME qiymatlarini to'ldiring."
  exit 1
fi
if grep -q "CHANGE_ME" backend/.env.production .env.mysql; then
  echo "ERROR: env fayllarda CHANGE_ME qolgan. Avval secretlarni to'ldiring."
  exit 1
fi

docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps