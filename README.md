# Backend VidéoTraduire

## Déploiement sur Render

1. Pousse ce dossier `backend/` sur un dépôt GitHub.
2. Sur [render.com](https://render.com) → **New** → **Web Service**.
3. Connecte ton dépôt. Render détectera le `Dockerfile` automatiquement
   (sinon choisis "Docker" comme environnement).
4. Dans **Environment Variables**, ajoute :
   - `OPENAI_API_KEY`
   - `ELEVENLABS_API_KEY`
   - `ELEVENLABS_VOICE_ID` (optionnel, une voix par défaut est utilisée sinon)
   - `ALLOWED_ORIGIN` → l'URL de ton site Netlify, ex: `https://ton-site.netlify.app`
5. Health check path : `/health`
6. Déploie. Ton URL sera du type `https://ton-service.onrender.com`.

## Mettre à jour le frontend

Dans `index.html`, remplace :
```js
const API_BASE_URL = 'https://REMPLACEZ-PAR-URL-DE-VOTRE-BACKEND.com';
```
par l'URL Render obtenue à l'étape 6.

## Limites anti-dérapage de facturation

Deux variables d'environnement protègent contre les factures surprises :

- `MAX_VIDEO_DURATION_SECONDS` (défaut : 300 = 5 min) — vidéo rejetée avant
  tout appel à OpenAI/ElevenLabs si elle dépasse cette durée.
- `MAX_UPLOAD_SIZE_MB` (défaut : 200) — fichier rejeté avant même d'être
  entièrement reçu s'il dépasse cette taille.

Ajuste-les selon ton offre commerciale (ex: 120s pour un plan gratuit,
600s pour un plan payant).

## Limites à connaître

- **Plan gratuit Render** : le service s'endort après 15 min d'inactivité,
  la première requête après une pause prend 30-50s pour redémarrer.
- **Stockage des jobs en mémoire** : si le serveur redémarre pendant un
  traitement, le job est perdu. Suffisant pour un MVP ; pour la prod,
  utiliser une base (Redis/Postgres) et un stockage fichier persistant
  (S3, Cloudinary...) puisque le disque de Render est éphémère.
- **Coûts API** : Whisper, GPT et ElevenLabs sont facturés à l'usage —
  surveille tes quotas, surtout ElevenLabs qui est le plus cher au caractère.
- **Vidéos longues** : le texte transcrit peut dépasser les limites de
  caractères d'ElevenLabs selon ton forfait ; pense à découper en segments
  pour les vidéos de plusieurs minutes si besoin.

## Test en local

```bash
cd backend
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
export ELEVENLABS_API_KEY=...
export ALLOWED_ORIGIN=http://localhost:8888
python app.py
```
(nécessite `ffmpeg` installé localement : `brew install ffmpeg` ou `apt install ffmpeg`)
