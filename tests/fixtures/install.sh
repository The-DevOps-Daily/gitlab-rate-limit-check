#!/bin/sh
# anonymous release download
curl -fsSLO "https://gitlab.com/acme/cli/-/releases/v1.4.0/downloads/cli-linux-amd64"
# authenticated with basic auth: fine
curl -fsS -u "deploy:$DEPLOY_TOKEN" "https://gitlab.com/api/v4/projects/42/jobs/artifacts/main/download?job=build" -o a.zip
# a comment that mentions curl https://gitlab.com/acme/x/-/raw/main/y is not a call
