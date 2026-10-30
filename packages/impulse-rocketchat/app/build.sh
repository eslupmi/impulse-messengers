#!/bin/sh
cd "$(dirname "$0")"
npm install
npx rc-apps package
