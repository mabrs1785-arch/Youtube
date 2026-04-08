# 📹 YouTube Faceless — Finance Personnelle FR

Pipeline 100% autonome : une vidéo par jour publiée sur YouTube, sans intervention.

## Architecture

```
daily_video.py          # Script principal (tout le pipeline)
topics_done.json        # Sujets déjà traités (auto-committé)
.github/workflows/      # GitHub Actions (14h UTC / 16h Paris)
```

**Pipeline** : Claude (script) → ElevenLabs (voix) → FFmpeg (vidéo) → YouTube (upload)

---

## Setup

### 1. Clé API Anthropic

- [console.anthropic.com](https://console.anthropic.com/) → API Keys → Create
- Secret GitHub : `ANTHROPIC_API_KEY`

### 2. ElevenLabs

- [elevenlabs.io](https://elevenlabs.io/) → Créer un compte
- Profile → API Key → copier
- Voice Lab → choisir/cloner une voix FR masculine → copier le Voice ID
- Secrets GitHub : `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

### 3. YouTube OAuth (le plus long)

#### a) Projet Google Cloud

1. [console.cloud.google.com](https://console.cloud.google.com/) → Nouveau projet
2. APIs & Services → Activer **YouTube Data API v3**
3. OAuth consent screen → External → remplir le minimum → ajouter scope `youtube.upload`
4. Credentials → Create → OAuth 2.0 Client ID → **Desktop app**
5. Télécharger le JSON → noter `client_id` et `client_secret`

#### b) Générer le Refresh Token

```bash
pip install google-auth-oauthlib
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_config(
    {'installed': {
        'client_id': 'VOTRE_CLIENT_ID',
        'client_secret': 'VOTRE_CLIENT_SECRET',
        'redirect_uris': ['http://localhost'],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }},
    scopes=['https://www.googleapis.com/auth/youtube.upload']
)
creds = flow.run_local_server(port=8080)
print('REFRESH TOKEN:', creds.refresh_token)
"
```

- Se connecter avec le compte YouTube cible
- Copier le refresh token affiché

#### c) Secrets GitHub

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

### 4. Configurer les secrets

Repo GitHub → Settings → Secrets and variables → Actions → New repository secret

Les 6 secrets :
| Secret | Source |
|---|---|
| `ANTHROPIC_API_KEY` | Console Anthropic |
| `ELEVENLABS_API_KEY` | Profile ElevenLabs |
| `ELEVENLABS_VOICE_ID` | Voice Lab ElevenLabs |
| `YOUTUBE_CLIENT_ID` | Google Cloud Console |
| `YOUTUBE_CLIENT_SECRET` | Google Cloud Console |
| `YOUTUBE_REFRESH_TOKEN` | Script ci-dessus |

---

## Utilisation

### Automatique
Le workflow tourne tous les jours à 14h UTC (16h Paris été). Rien à faire.

### Manuel
Actions → Daily YouTube Video → Run workflow

### Test local
```bash
export ANTHROPIC_API_KEY=sk-...
export ELEVENLABS_API_KEY=...
export ELEVENLABS_VOICE_ID=...
python daily_video.py --dry-run
```

Le `--dry-run` fait tout sauf l'upload YouTube et copie les fichiers (MP3, MP4, thumbnail) dans le dossier courant.

---

## Coûts estimés (~€15-20/mois)

| Service | Coût/vidéo | Coût/mois (30 vidéos) |
|---|---|---|
| Claude Sonnet | ~€0.05 | ~€1.50 |
| ElevenLabs | ~€0.30 | ~€9 |
| YouTube API | Gratuit | Gratuit |
| GitHub Actions | Gratuit (2000 min/mois) | Gratuit |

---

## Personnalisation

- **Nom de chaîne** : modifier `CHANNEL_NAME` dans `daily_video.py`
- **Fuseau horaire** : modifier `PARIS_TZ` (hiver = `+1`, été = `+2`)
- **Style visuel** : modifier les couleurs dans `generate_video()` et `generate_thumbnail()`
- **Ton du script** : modifier `SYSTEM_PROMPT`
