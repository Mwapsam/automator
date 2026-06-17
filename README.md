**FIELD_ENCRYPTION_KEY** ***set or the app will raise an error on startup. Generate one with:***    
    `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`



curl -i -X POST https://api.progstack.org/api/login -H 'Content-Type: application/json' -d '{"username":"postmaster@progstack.org","password":"@Hello2061#"}'

## Frontend / static assets

The UI is server-rendered Django templates styled with **compiled Tailwind CSS**
(design tokens in `assets/app.css`) plus self-hosted **Alpine.js**. No Node/npm —
styling is built with the Tailwind **standalone CLI**.

One-time: download the CLI binary into `tools/` (gitignored):

```
# Windows x64 (adjust the asset name for macOS/Linux)
curl -L -o tools/tailwindcss.exe \
  https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-windows-x64.exe
```

Build the stylesheet (re-run after changing templates or `assets/app.css`):

```
tools/tailwindcss.exe -i assets/app.css -o static/css/app.css --minify
# or: tools/tailwindcss.exe -i assets/app.css -o static/css/app.css --watch
```

The built `static/css/app.css` and vendored `static/js/*` and `static/fonts/*`
are committed, so the running app has styles without needing the CLI present.
In production, `python manage.py collectstatic` compresses + cache-busts them
(served by WhiteNoise).
