TLS files for `ui.candidate.ferus.tech`

Put certificate files into this folder on the host:

- `nginx/certs/fullchain.pem`
- `nginx/certs/privkey.pem`

These files are mounted to the nginx container as:

- `/etc/nginx/certs/fullchain.pem`
- `/etc/nginx/certs/privkey.pem`

If your provider gives separate files (`certificate.crt` + `ca_bundle.crt` + `private.key`):

1. Save private key as `nginx/certs/privkey.pem`.
2. Build full chain as `nginx/certs/fullchain.pem` by concatenating:
   - server certificate first
   - intermediate/root CA bundle after it

Example (Linux):

```bash
cat certificate.crt ca_bundle.crt > nginx/certs/fullchain.pem
cp private.key nginx/certs/privkey.pem
```

After copying files, restart nginx:

```bash
docker compose up -d --force-recreate nginx
```
