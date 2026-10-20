# ⚙️ Guide de Déploiement, Dimensionnement CPU & Multi-DMD

> **[🇬🇧 Read this guide in English (DEPLOYMENT_AND_SIZING.md)](DEPLOYMENT_AND_SIZING.md)**

`dmd-media-showcase` a été conçu dès le départ pour tourner de manière ultra-stable aussi bien sur un petit **Raspberry Pi 4 / NAS Synology** que sur une **VM Proxmox / Unraid / Docker** alimentant toute une Gameroom de panneaux DMD.

---

## 1. Comment le Pool de Workers Borné Protège Votre Serveur

Le découpage vidéo, l'analyse IA des scènes d'action et le traitement colorimétrique LED sont des opérations gourmandes en CPU. Sans contrôle strict, 5 DMD réclamant 10 vidéos simultanément lanceraient 50 processus FFmpeg en parallèle, provoquant une saturation CPU à 100 %, une surchauffe thermique ou un crash mémoire (OOM).

`dmd-media-showcase` élimine ce risque grâce à son **Pool de Workers FIFO Borné (`SessionBatchManager`)** :
1. **Plafond Strict de Concurrence (`MAX_CONCURRENT_JOBS`) :** Seuls `1` à `N` processus de conversion tournent simultanément (`ProcessPoolExecutor`).
2. **FFmpeg Mono-Thread par Job (`FFMPEG_THREADS_PER_JOB=1`) :** Chaque worker bride OpenCV et FFmpeg à un seul thread pour garantir une empreinte CPU et RAM 100 % prédictible.
3. **Priorité OS Abaissée (`WORKER_NICE_INCREMENT=10`) :** Les workers vidéo tournent en `nice +10`, garantissant que la boucle réseau FastAPI (HTTP & Streaming RGB565) conserve toujours la priorité CPU absolue (temps de réponse `< 2 ms`).
4. **Saut de Rotation Élastique côté DMD :**
   - **Serveur Rapide (4–8 vCPU) :** Les lots sont prêts bien avant que le tour de rotation du DMD n'arrive $\rightarrow$ **0 saut de rotation**.
   - **Serveur Modeste (1–2 vCPU) avec beaucoup de DMD :** Les demandes s'empilent sagement dans la file FIFO. Lorsqu'un DMD interroge `GET /api/v1/{domain}/batch` (*« Es-tu prêt ? »*) et reçoit `status: "warming"`, il **saute simplement son tour de rotation** et passe au moteur suivant (Horloge, Météo, Scores, GIF) pendant que le serveur termine la préparation en tâche de fond.
   - **Signal Auto-Diagnostique :** Si vous constatez qu'un DMD saute plusieurs rotations d'affilée avant d'afficher son Showcase, c'est l'indicateur visuel naturel qu'il suffit soit de réduire le nombre de moteurs Showcase simultanés, soit d'allouer un vCPU supplémentaire à votre VM.

---

## 2. Tableau de Dimensionnement Matériel & VM Recommandé

| Profil Hôte / VM | vCPU / Cœurs | RAM | `MAX_CONCURRENT_JOBS` Recommandé | Capacité Typique (DMD Simultanés) |
| :--- | :--- | :--- | :--- | :--- |
| **Raspberry Pi 4 / NAS Synology** | 2 vCPU | 512 Mo – 1 Go | `1` | **1 à 2 DMD** (3–5 items/rotation) |
| **VM Domotique Standard (Proxmox / Unraid)** | 2 à 4 vCPU | 1 Go – 2 Go | `2` | **3 à 6 DMD** (5–10 items/rotation) |
| **Serveur Gameroom / Core i5 / Ryzen** | 6 à 8 vCPU | 2 Go – 4 Go | `4` | **8 à 15+ DMD** (zéro saut de rotation) |

---

## 3. Référence des Variables d'Environnement (`.env`)

| Variable | Défaut | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | Port d'écoute HTTP du serveur. |
| `DEFAULT_LANG` | `fr` | Langue ISO par défaut (`fr`, `en`, `es`, `de`, `it`). |
| `MAX_CONCURRENT_JOBS` | `0` (Auto: `1..4`) | Nombre maximal de conversions vidéo simultanées (`1` sur RPi/petit NAS, `2..4` sur VM). |
| `FFMPEG_THREADS_PER_JOB` | `1` | Nombre de threads par worker FFmpeg/OpenCV. Laisser à `1` pour un lissage multi-DMD optimal. |
| `WORKER_NICE_INCREMENT` | `10` | Incrément de courtoisie POSIX `nice` appliqué aux workers vidéo (`0..19`). |
| `CACHE_DIR` | `/tmp/dmd_media_showcase_cache` | Dossier éphémère des lots `CurrentBatch` et `NextBatch` (monté en RAM `tmpfs` sous Docker). |
| `BATCH_TTL_SECONDS` | `180` | Durée de vie minimale d'un lot servi avant bascule sur `NextBatch` et purge des anciens fichiers. |
| `SESSION_TTL_SECONDS` | `1200` | Délai d'inactivité (20 min) après lequel une session DMD inactive et ses fichiers sont purgés. |
| `SERVER_MASTER_CLIP_MAX_S` | `12.0` | Durée plafond du clip maître en cache permettant la troncature dynamique $O(1)$ via `?max_duration_s=`. |
