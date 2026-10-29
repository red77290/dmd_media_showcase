# Guía de Integración ESP32 y Receptor Pasivo — DMD Media Showcase Server

🇬🇧 [English](ESP32_INTEGRATION.md) | 🇫🇷 [Français](ESP32_INTEGRATION_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

**DMD Media Showcase Server** soporta tres topologías complementarias de integración para paneles LED HUB75 basados en **ESP32 y ESP32-S3**. El **Modelo 1** (DMD Multimotor controlado por el cliente, ej. `ArcadeMatrix` ESP32 / `ArcadeMatrix_RPi`) y el **Modelo 2A** (Receptor Híbrido gestionado por el servidor, `supports_local_card: true`) combinan el **plano de control MQTT `:1883`** (`config`/`hello`, `ready`, `start_card`) con **Streaming HTTP TCP `DMDS` con Marcapasos en Tiempo Real (`:8890`, `MQTT+TCP` por defecto)**, mientras que el **Modelo 2B** (`supports_local_card: false`) ofrece un receptor opcional ultramínimo (~180 líneas de C++) **100 % Pasivo UDP `:4210` `DMDF`** (`UDP-PASSIVE`).

> [!IMPORTANT]
> **Por Qué `MQTT + TCP` (`DMDS` `:8890`) es el Transporte por Defecto y Recomendado para los Modelos 1 y 2A**
> 1. **Cero Pérdidas NAT UDP en VMs de Docker Desktop / Rancher Desktop**: El tráfico entrante HTTP TCP `:8890` (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true&paced=1`) atraviesa de forma nativa todos los puentes de contenedores y el NAT de la VM anfitriona sin bloqueos de retorno UDP ni cortafuegos.
> 2. **Cero Sobrecarga de Reensamblado MTU**: Los fotogramas RGB565LE contiguos llegan en orden de flujo (cabecera Little-Endian `<4sBBHHHHH` `DMDS` de `16 bytes` + `width * height * 2` bytes/fotograma) sin máscaras de bits por paquete.
> 3. **Cero Heap en el Cliente y Cero Bloqueos `TCP_WND`**: Los clientes embebidos abren un `lwip_socket` puro (`HTTP/1.1`, `Connection: close`, omitiendo `drop_late=1` para preservar `Content-Length` y recibir **binario `DMDS` 100 % puro sin `chunked`** desde Uvicorn) leyendo directamente en `m_frame565`. El marcapasos monotónico en el servidor (`X-DMD-Paced: 1`) entrega fotogramas al ritmo objetivo con reanclaje automático de reloj para que el ESP32 nunca duerma con bytes sin leer (`0` bloqueos `TCP ZeroWindow`), mientras que `ArcadeMatrix` incluye además un `HttpChunkedDecoder` cero-heap de respaldo (`drop_late=1` omite `Content-Length` / activa `Transfer-Encoding: chunked` para clientes HTTP/1.1 completos como `httpx` en Python).
> 4. **Identificadores `item_id` Compactos sin Colisión (`<= 24` caracteres)**: Cada paquete preparado emplea `pkg_<domain>_<12hex>` (ej. `pkg_games_a1b2c3d4e5f6`), cabiendo en búferes fijos de pila en `ESP32_STD` con cero asignación dinámica.

---

## 1. Visión General y Comparación de las 3 Topologías de Integración ESP32

### Modelo 1 — DMD Multimotor Controlado por el Cliente (`config_source: "client"`, `MQTT+TCP` por defecto)

Diseñado para firmwares avanzados donde el ESP32 gestiona su propia rotación local de pantallas (`Clock`, `Weather`, `Scoreboard`, `AudioSpectrum`, `RetroGames`, `Movies`, `Series`, `Anime`), su pila de preempción (`DisplayArbiter`) y su propia WebUI integrada.

- **Autoridad de Configuración**: La WebUI del ESP32 consulta dinámicamente `GET /api/v1/{domain}/filters?lang={ui_lang}` para poblar sus listas desplegables localizadas, guarda los filtros localmente y publica su configuración retenida en `dmd_showcase/clients/{dmd_id}/config` (`retain=True`).
- **Sesión MQTT Acotada + Streaming TCP `DMDS` con Marcapasos**: Core 0 reclama la ficha `ready` retenida por MQTT (`start_card`), **cierra inmediatamente el socket TCP MQTT** (`0` sockets abiertos durante la Fase 1 de la Ficha) y reproduce la Fase 2 (`Panoramas + Capturas + Vídeo`) mediante TCP `DMDS` puro en LwIP (`:8890`, `?purge=true&paced=1`).
- **Comportamiento en Rotación**: Cuando llega el turno de un motor Showcase y el slot MQTT retenido (`dmd_showcase/clients/{dmd_id}/{domain}/ready`) indica `{"ready": false}`, el motor cede su turno en **`0 ms`** (`m_skipTurnImmediately = true`). El panel pasa al instante al siguiente motor local (`Clock`, `Weather`, etc.) sin pantallas negras ni bloqueos.
- **Implementación de Referencia en Producción (`ArcadeMatrix`)**:
  - Cabecera y Contrato de Motor: [`src/engines/MediaShowcaseEngines.h`](https://github.com/red77290/ArcadeMatrix/blob/main/src/engines/MediaShowcaseEngines.h)
  - Worker MQTT Acotado, Parser `ArduinoJson` Zero-Copy y Streamer TCP `DMDS` Puro LwIP: [`src/engines/MediaShowcaseEngines.cpp`](https://github.com/red77290/ArcadeMatrix/blob/main/src/engines/MediaShowcaseEngines.cpp)

---

### Modelo 2A — Receptor Híbrido Gestionado por el Servidor (`config_source: "server"`, `supports_local_card: true`, `MQTT+TCP` por defecto)

Diseñado para paneles Showcase dedicados que desean mantener el desplazamiento de texto local a 60 FPS en el ESP32 **sin** incorporar WebUI local, lógica de catálogos ni esquemas de filtros en NVS.

- **Anuncio de Hardware Ligero**: Al arrancar, el ESP32 publica un mensaje retenido mínimo en `dmd_showcase/clients/{dmd_id}/hello` (`retain=True`) anunciando únicamente su geometría e IP LAN (`width`, `height`, `config_source: "server"`, `supports_local_card: true` e idioma base `lang`).
- **Perfiles Centralizados Persistentes (`./data/dmd_profiles.json`)**: Todas las listas de reproducción (`games`, `movies`, `series`, `anime`), categorías (`systems`, `genres`, `decades`), ajustes de encuadre/colorimetría IA (`video_mode`, `color_boost`) y el idioma del contenido emitido (`lang`) se configuran en el **Panel Web de Showcase** (`http://dmdmediashowcase.local:8890/`) y se guardan en `./data/dmd_profiles.json`.
- **Renderizado Híbrido**: El ESP32 dibuja localmente la **Fase 1 (Ficha Informativa)** a 60 FPS (minicarátula + insignia de color + sinopsis fluida recibida vía `ready` o `dmd_showcase/clients/{dmd_id}/command` `play_card`, confirmada con `start_card`) y abre `GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true&paced=1` en `:8890` para recibir la **Fase 2 (Panoramas + Vídeo)** por HTTP TCP `DMDS` con marcapasos (con soporte opcional `mqtt_udp` `start_stream`).

---

### Modelo 2B — Receptor Framebuffer 100 % Pasivo Gestionado por el Servidor (`config_source: "server"`, `supports_local_card: false`, `UDP-PASSIVE`)

Diseñado para montajes ultramínimos y sin mantenimiento (`ESP32_STD` WROOM-32 **sin PSRAM** o `ESP32-S3`) en **~180 líneas de C++** — **cero WebUI, cero fuentes tipográficas, cero parser JSON y cero librería MQTT necesaria**.

- **Descubrimiento Cero-Configuración**: El ESP32 se anuncia mediante un heartbeat HTTP periódico `POST /api/v1/clients/hello` (o MQTT `dmd_showcase/clients/{dmd_id}/hello`), o incluso **sin ningún tráfico saliente** configurando una IP LAN estática con `always_stream_udp: true` en `./data/dmd_profiles.json`.
- **Stream 100 % Renderizado en el Servidor**: Con `supports_local_card: false`, el servidor Showcase renderiza **incluso la Ficha Informativa de la Fase 1** (minicarátula + insignia coloreada + título + año + puntuación + sinopsis localizada en desplazamiento) directamente dentro del stream RGB565 y envía fragmentos UDP `DMDF` (`<= 1408 B` útiles) hacia el puerto `:4210`.
- **Control en Vivo Instantáneo**: Las acciones `Skip`, `Pause`, `Resume`, `Stop` y `Play Now` (`FrontendSync`) se ejecutan **100 % en el servidor**, conmutando el hilo `FfmpegUdpClockStreamer` hacia `:4210` en `< 50 ms`.

---

### Tabla Comparativa de las 3 Topologías

| Criterio | Modelo 1: Controlado por Cliente (`ArcadeMatrix`) | Modelo 2A: Híbrido Gestionado por Servidor | Modelo 2B: Receptor UDP 100 % Pasivo |
| :--- | :--- | :--- | :--- |
| **Anuncio y Transporte** | `config_source: "client"` (`MQTT+TCP` por defecto) | `config_source: "server"`, `supports_local_card: true` (`MQTT+TCP` por defecto) | `config_source: "server"`, `supports_local_card: false` (`UDP-PASSIVE` `:4210`) |
| **Almacenamiento de Filtros** | Flash ESP32 (`config.json`) | Servidor (`./data/dmd_profiles.json`) | Servidor (`./data/dmd_profiles.json`) |
| **Complejidad Firmware / UI** | OS Multimotor completo + WebUI | ~320 líneas C++ (MQTT acotado + TCP `DMDS` LwIP + Fuente) | **~120–180 líneas C++** (Cero UI, Cero Fuentes, Cero JSON) |
| **Fase 1 (Ficha Informativa)** | Local 60 FPS en Core 1 del ESP32 | Local 60 FPS en Core 1 del ESP32 | **Renderizada por el servidor en el stream UDP RGB565** |
| **Fase 2 (Panoramas + Vídeo)** | **HTTP TCP `DMDS` con marcapasos (`:8890`, `?purge=true&paced=1`)** *(opción `mqtt_udp`)* | **HTTP TCP `DMDS` con marcapasos (`:8890`, `?purge=true&paced=1`)** *(opción `mqtt_udp`)* | **UDP Pasivo `DMDF` (`:4210`)** |
| **Consumo SRAM (`128x32`)** | `8 KB` frame + `2 KB` (`32x32`) / `6 KB` (`48x64`) carátula | `8 KB` frame + `2–6 KB` carátula | **`8 KB` simple (`16 KB` ping-pong) — `0 B` carátula/JSON** |
| **Soporte `ESP32_STD` (Sin PSRAM)** | Cualificado (`128x32` a **HUB75 8 bits**, `64x64`, `128x64`) | Cualificado (`128x32` a `128x64`) | **Cualificado (`128x32` hasta `256x64` buffer simple)** |
| **Carga CPU Core 0** | Baja (MQTT acotado + Base64 + `lwip_recv` en `frame565`) | Baja (MQTT acotado + Base64 + `lwip_recv` en `frame565`) | **Mínima (puro `memcpy` UDP no bloqueante)** |
| **Respuesta `Skip` / `Pause` / `Play Now`** | Cola prioritaria (`N+1`) o MQTT `.../command` | Vía MQTT `.../command` (`play_card` / `skip`) | **Instantánea (`< 50 ms`) directo en el streamer `DMDF`** |

---

## 2. Coreografía FreeRTOS de Doble Núcleo e Invariantes DMA HUB75

Los paneles LED HUB75 carecen de controlador de memoria propio: el periférico I2S/LCD DMA del ESP32 debe barrer continuamente los planos de bits PWM desde la SRAM interna. Cualquier ráfaga de interrupciones Wi-Fi, reserva dinámica de memoria o bloqueo por mutex en el núcleo de renderizado provoca parpadeos visibles.

Para garantizar **60 FPS constantes** (`16,67 ms` por fotograma), la arquitectura impone un aislamiento estricto entre ambos núcleos:

- **Core 0 (`PRO_CPU` — Worker de Red y Protocolo)**:
  - Ejecuta la pila Wi-Fi / LwIP y la resolución mDNS (`dmdmediashowcase.local` en `224.0.0.251:5353`).
  - **Modelos 1 y 2A (`MQTT+TCP` por defecto)**: Ejecuta una breve **sesión MQTT `:1883` acotada** (`config` o `hello` $\to$ `ready` retenido $\to$ `start_card` $\to$ cierre inmediato del socket TCP dejando `0` sockets abiertos durante la Ficha), y después abre un `lwip_socket` puro cero-heap (`HTTP/1.1`, `Connection: close`, `TCP_NODELAY`, `SO_RCVTIMEO` de `250 ms`) hacia `:8890` (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true&paced=1`), leyendo la **cabecera Little-Endian `<4sBBHHHHH` `DMDS` de 16 bytes** seguida de fotogramas RGB565LE contiguos con marcapasos en el servidor (`X-DMD-Paced: 1`, `Content-Length` sin `chunked`) directamente en `frame565`.
  - **Modelo 2B (`UDP-PASSIVE :4210`)**: Ejecuta el heartbeat HTTP `POST /api/v1/clients/hello` y reensambla datagramas **UDP `DMDF`** no bloqueantes (cabecera `<4sHBBH` de `10 bytes` + fragmento `<= 1408 bytes` RGB565LE = `<= 1418 bytes` por paquete UDP, cero fragmentación IP bajo el MTU Wi-Fi de `1500 bytes`).
- **Core 1 (`APP_CPU` — Bucle de Renderizado HUB75 a 60 FPS)**:
  - Controla exclusivamente `IDrawingSurface` / DMA HUB75 a 60 FPS.
  - **Cero Asignación Dinámica**: Jamás invoca `malloc`, `new`, `String` ni `std::vector::push_back` en la ruta crítica.
  - **Cero Mutex / Cero Contención**: Jamás adquiere `std::mutex` ni semáforos compartidos con Core 0.
  - **Aislamiento Estricto de DMA**: Nunca escribe paquetes de red directamente sobre el buffer físico DMA en barrido activo; todo pasa por el framebuffer intermedio `frame565` y `IDrawingSurface`.

### Transferencia Lock-Free entre Núcleos: `frameSeq` Par/Impar + `consumedFrameSeq` vs Doble Buffer Ping-Pong

1. **Sincronización Sin Tearing `frameSeq` Par/Impar + `consumedFrameSeq` (`frame565` Único — Modelos 1 y 2A por defecto)**:
   - Probada en producción en [`MediaShowcaseEngines.cpp`](https://github.com/red77290/ArcadeMatrix/blob/main/src/engines/MediaShowcaseEngines.cpp) para eliminar el tearing horizontal con **un único búfer `frame565`** (`8 KB` en `128x32`) en `ESP32_STD`.
   - Core 0 espera el siguiente fotograma TCP con marcapasos del servidor en `lwip_select` (rebanadas de `25 ms`) **mientras `frameSeq` permanece `PAR`**, de modo que Core 1 jamás se bloquea durante los intervalos entre fotogramas (`40–66 ms`).
   - Cuando llegan los bytes a `TCP_WND` de LwIP, Core 0 espera como máximo `18 ms` (`1` refresco HUB75) a que Core 1 haya presentado el fotograma previo (`consumedFrameSeq == frameSeq`), pasa `frameSeq` a **`IMPAR`** (`fetch_add(1, memory_order_acq_rel)`), lee `FRAME_BYTES` directamente con `lwip_recv` en `frame565` (`~1,2 ms`) y devuelve `frameSeq` a **`PAR`** (`fetch_add(1, memory_order_release)`).
   - Core 1 verifica `(fSeq & 1U) == 0U && fSeq != lastRenderedSeq`, copia `frame565` y registra `consumedFrameSeq.store(fSeq, memory_order_release)`. Con `X-DMD-Paced: 1`, Core 0 realiza **cero pausas `vTaskDelay`** tras cada fotograma, manteniendo `TCP_WND` 100 % abierta.
2. **Doble Buffer Ping-Pong `frame565[2]` (`std::atomic<uint8_t> frontIdx` — Modelo 2B UDP `DMDF`)**:
   - Core 0 copia los fragmentos `DMDF` en `frame565[backIdx]` marcando `(1ULL << chunk_idx)` en `chunkMask`.
   - Al completarse `chunkMask == ((1ULL << total_chunks) - 1ULL)`, Core 0 publica atómicamente `frontIdx.store(backIdx, std::memory_order_release)`. Core 1 lee siempre un fotograma íntegro.

### Presupuestos Exactos de SRAM / PSRAM por Geometría HUB75

Cualquier **`ESP32_STD` sin PSRAM** (WROOM-32 con ~`110–140 KB` de heap interno libre tras iniciar Wi-Fi + HUB75 DMA) ejecuta `dmd_media_showcase` con total fluidez:

| Geometría del Panel | Píxeles (`An x Al`) | TCP `DMDS` / UDP `DMDF` (`<= 1408 B`) | `frame565` Simple (Modelos 1 y 2A) | Ping-Pong `frame565[2]` (Modelo 2B) | `poster565` Ficha Local (Modelo 1 / 2A) | Estado `ESP32_STD` Sin PSRAM |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`128x32`** *(DMD WPC)* | `4.096 px` | `0` fragmentos TCP / `6` fragmentos UDP | **`8.192 B`** (`8 KB`) | `16.384 B` (`16 KB`) | `2.048 B` (`32x32`) | **Fluido a profundidad HUB75 de 8 bits en `ESP32_STD`** |
| **`64x64`** *(Cuadrado)* | `4.096 px` | `0` fragmentos TCP / `6` fragmentos UDP | **`8.192 B`** (`8 KB`) | `16.384 B` (`16 KB`) | `2.048 B` (`32x32`) | **Fluido en `ESP32_STD`** |
| **`128x64` / `64x128` TATE** | `8.192 px` | `0` fragmentos TCP / `12` fragmentos UDP | **`16.384 B`** (`16 KB`) | `32.768 B` (`32 KB`) | `6.144 B` (`48x64`) / `3.200 B` (`40x40`) | **Cabe en SRAM interna `ESP32_STD` (ajuste auto HUB75 4 bits si es necesario)** |
| **`256x64`** *(Super-DMD)* | `16.384 px` | `0` fragmentos TCP / `24` fragmentos UDP | **`32.768 B`** (`32 KB`) | `65.536 B` (`64 KB`) | `6.144 B` (`48x64`) | **`32 KB` Simple en `ESP32_STD`; `ESP32-S3` PSRAM recomendado** |

### Pipeline de Visualización HUB75 Recomendado: `Single DMA Buffer + Offscreen RGB565 Canvas` (`canvas_single`)

En todos los firmwares ESP32 HUB75 (incluido [`ArcadeMatrix`](https://github.com/red77290/ArcadeMatrix)), el pipeline **`Single DMA Buffer + Offscreen RGB565 Canvas` (`canvas_single`)** sobre [`mrfaptastic/ESP32-HUB75-MatrixPanel-I2S-DMA`](https://github.com/mrfaptastic/ESP32-HUB75-MatrixPanel-I2S-DMA) es **la arquitectura de referencia recomendada**:

1. **Ahorro del 50 % en SRAM Interna Compatible con DMA (`mxconfig.double_buff = false`)**:
   - En `ESP32_STD` (sin PSRAM), los descriptores DMA I2S y los planos de bits BCM deben residir en DRAM interna contigua (`MALLOC_CAP_DMA`), exactamente el mismo recurso demandado por la pila Wi-Fi + LwIP TCP/UDP.
   - Desactivar el doble buffer DMA por hardware (`mxconfig.double_buff = false`) reduce a la mitad la memoria de planos de bits, mientras que el lienzo lineal RGB565 fuera de pantalla (`GFXcanvas16`) solo requiere `2 bytes/píxel` (`8 KB` en `128x32`, `16 KB` en `128x64`, y puede residir en PSRAM en `ESP32-S3`).
2. **Profundidad de Color HUB75 Completa de 8 Bits en `ESP32_STD` (`esp32dev`) mediante Preempción `WEBUI_SYNC`**:
   - Los firmwares multimotor (`ArcadeMatrix`) mantienen **la profundidad completa HUB75 BCM de 8 bits** en paneles `128x32` sobre placas `ESP32_STD` (`esp32dev` sin PSRAM) durante la reproducción normal de Showcase — bajando automáticamente a `4 bits` **únicamente** cuando la RAM interna libre medida lo exige (por ejemplo en `128x64` sin PSRAM).
   - Al cargar inicialmente la SPA WebUI integrada (`GET /`), `ArcadeMatrix` activa temporalmente una fuente de visualización prioritaria cero-heap **`WEBUI_SYNC`** ([`WebUiSyncEngine`](https://github.com/red77290/ArcadeMatrix/blob/main/src/engines/WebUiSyncEngine.h)) en `PreemptionStack`. Desalojar el motor activo (`deactivate()`) libera al instante **`~48 KB` de SRAM interna** (`m_frame565`, `m_poster565`, `m_ioScratch` y búferes TCP) para servir la SPA comprimida sin errores de memoria, reanudando automáticamente la rotación Showcase a 8 bits en cuanto termina la carga.
3. **Cero Parpadeo Negro al Limpiar (Sobrescritura de Planos de Bits en Sitio)**:
   - Dibujar directamente sobre un único buffer DMA (`direct_single`) invocando `clearScreen()` apaga los planos de bits en pleno barrido activo, produciendo un parpadeo negro visible.
   - Con un **Lienzo RGB565 Fuera de Pantalla**, todo borrado, trazado `Adafruit_GFX` (texto, HUD, puntuación), animación de espera y actualización de fotogramas `DMDS`/`DMDF` ocurre exclusivamente en RAM fuera de pantalla. `blitCanvas565()` sobrescribe después cada palabra de plano de bits HUB75 **en sitio** (`(dmaRow[ax] & ~0x003F) | rgb12`) en **`~300 µs`** (`128x32`) sin pasar jamás por un borrado previo en negro.
4. **Activación por Bandera `Dirty` (`frameSeq != lastPresentedSeq`)**:
   - Core 1 opera a `60 Hz` (`16,6 ms`) mientras que el vídeo TCP `DMDS` / UDP `DMDF` llega a `15–25 FPS` (`40–66 ms`).
   - Al volcar el lienzo a `frame_buffer[0]` **únicamente cuando un nuevo fotograma ha modificado realmente el lienzo**, Core 1 evita cualquier escritura DMA redundante entre fotogramas de vídeo, eliminando la contención del bus SRAM durante el barrido I2S.
5. **Codificación Burst BCM Multiplexada + Gamma Perceptual CIE 1931 + Ajuste FIFO I2S en ESP32 Clásico**:
   - Codifica simultáneamente la semifila superior `(y)` y la inferior `(y + H/2)` en `R1G1B1R2G2B2` usando tablas **gamma CIE 1931** escaladas a la profundidad activa, aplica el intercambio de palabras de 16 bits del FIFO I2S del ESP32 original (`MATRIX_TX_ADJUST(x)`) y vuelca las líneas de caché de CPU (`Cache_WriteBack_Addr`) cuando `SPIRAM_DMA_BUFFER` está habilitado en `ESP32-S3`.

| Modo de Pipeline | `mxconfig.double_buff` | Lienzo RGB565 Fuera de Pantalla | Margen DRAM DMA (`ESP32_STD`) | Parpadeo / Tearing | Recomendación |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **`canvas_single`** | **`false`** | **Sí (`GFXcanvas16`)** | **Óptimo (-50 % DRAM DMA)** | **Cero parpadeo (burst `~300 µs` en sitio + dirty gating)** | **Recomendado (`ESP32_STD` y `ESP32-S3`)** |
| `canvas_double` | `true` | Sí (`GFXcanvas16`) | Alto consumo RAM (`2x` DMA + Lienzo) | Intercambio sin tearing | Opcional en `ESP32-S3` Octal PSRAM |
| `direct_double` | `true` | No | Alta presión DRAM (`2x` planos) | `drawPixel()` lento en vídeo | Solo sketches GFX heredados |
| `direct_single` | `false` | No | Máxima RAM libre | Parpadeo visible con `clearScreen()` | Solo emergencia de ultra baja RAM |

---

## 3. Secuenciación Completa de Tareas, Estados de Espera y Recuperación por Watchdog

### Diagrama de Secuencia de Extremo a Extremo

```mermaid
sequenceDiagram
    participant C1 as "ESP32 Core 1 (60 FPS HUB75)"
    participant C0 as "ESP32 Core 0 (MQTT acotado + TCP LwIP)"
    participant SRV as "Servidor Showcase (:1883 MQTT / :8890 HTTP)"
    participant UDP as "FfmpegUdpClockStreamer (Modo 2B :4210)"

    Note over C1,C0: "1. ARRANQUE Y DESCUBRIMIENTO mDNS / MQTT ACOTADO"
    C1->>C1: "Animacion pixel-art procedimental 60 FPS no bloqueante"
    C0->>SRV: "Resolucion mDNS dmdmediashowcase.local + MQTT CONNECT (con LWT)"
    C0->>SRV: "PUBLISH clients/{id}/config O clients/{id}/hello (retain=True)"

    Note over C1,SRV: "2. PREPARACION / ESPERA DE CONTENIDO (ready: false)"
    SRV-->>C0: "Retained ready: false (preparando pkg_<domain>_<12hex> en segundo plano)"
    alt "DMD Multimotor (ArcadeMatrix, rotationSlots > 1)"
        C0->>C1: "m_skipTurnImmediately = true (salto en 0 ms hacia Reloj/Clima)"
    else "Receptor Dedicado / Pasivo (Modelos 2A y 2B)"
        C1->>C1: "Mantiene animacion fluida a 60 FPS (cero pantalla negra)"
    end

    Note over C1,SRV: "3. FASE 1 — CLAIM MQTT ACOTADO Y FICHA LOCAL 60 FPS"
    SRV-->>C0: "Entrega ready: true + Ficha N (pkg_<domain>_<12hex>)"
    C0->>SRV: "PUBLISH {domain}/events {action: start_card, item_id: N} -> MQTT DISCONNECT"
    Note over C0: "¡0 sockets TCP/UDP abiertos durante el desplazamiento de la Ficha!"
    SRV->>SRV: "Bloquea N en seen_history.json + inicia preparacion Standby N+1"
    C1->>C1: "Desplazamiento de sinopsis localizada 60 FPS y minicaratula (5-12 s)"

    Note over C1,UDP: "4. FASE 2 — STREAM HTTP TCP DMDS CON MARCAPASOS (DEFECTO) O UDP PASIVO DMDF"
    alt "Modo Principal MQTT+TCP (Modelos 1 y 2A — Por defecto)"
        C0->>SRV: "LwIP HTTP/1.1 GET /api/v1/{domain}/stream/{N}.rgb565.stream?purge=true&paced=1"
        SRV-->>C0: "200 OK (Content-Length + X-DMD-Paced: 1, Cabecera DMDS 16B + Fotograma 0)"
        loop "Fotogramas Sincronizados por el Servidor 15-25 FPS (Cero Bloqueo TCP_WND)"
            SRV-->>C0: "Fotograma RGB565LE contiguo (W * H * 2 B) -> lwip_recv en frame565"
            C0->>C1: "Transferencia sin tearing frameSeq Par/Impar + consumedFrameSeq"
        end
        SRV->>SRV: "EOF HTTP -> purge_consumed_package(N) inmediata en disco"
    else "Modelo 2B Pasivo (UDP-PASSIVE :4210, supports_local_card: false)"
        UDP-->>C0: "UDP DMDF (Cabecera 10B <4sHBBH + <=1408B RGB565LE) hacia :4210"
        C0->>C1: "Intercambio Ping-Pong al completar chunkMask (watchdog 2500 ms)"
    end

    Note over C1,SRV: "5. DESACTIVACION / PREEMPCION (< 250 ms CIERRE QUIESCENTE)"
    C0->>SRV: "lwip_close(sock) -> TCP FIN dispara purga inmediata en disco de N"
    C0->>C1: "Cierre quiescente Core 0 -> liberacion segura de buffers"
```

### Máquina de Estados Determinista en el ESP32

```mermaid
stateDiagram-v2
    [*] --> BootDiscovery
    BootDiscovery: "1. ARRANQUE Y DESCUBRIMIENTO (Wi-Fi + mDNS + MQTT Acotado / Hello HTTP)"
    WarmingWait: "2. PREPARACION / ESPERA (ready == false)"
    SkipZeroMs: "Ceder Turno en 0 ms (Rotacion Multimotor)"
    Phase1Card: "3. FASE 1: FICHA INFO (60 FPS Local, 0 Sockets Abiertos)"
    Phase2Stream: "4. FASE 2: TCP DMDS CON MARCAPASOS (:8890) O UDP PASIVO DMDF (:4210)"
    QuiescentStop: "5. DESACTIVACION QUIESCENTE (lwip_close -> unir tarea -> free RAM)"

    BootDiscovery --> WarmingWait: "Conectado (slot preparandose)"
    BootDiscovery --> Phase1Card: "Retained ready == true -> start_card -> close MQTT (Modelo 1 / 2A)"
    BootDiscovery --> Phase2Stream: "Llega primer frame UDP DMDF (Modelo 2B)"

    WarmingWait --> SkipZeroMs: "m_skipTurnImmediately (ArcadeMatrix >1 motor)"
    WarmingWait --> Phase1Card: "ready == true / command: play_card"
    WarmingWait --> Phase2Stream: "Inicio stream UDP DMDF (Modelo 2B)"

    Phase1Card --> Phase2Stream: "Fin de sinopsis -> GET .rgb565.stream?purge=true&paced=1"
    Phase1Card --> Phase1Card: "command: pause / resume"
    Phase1Card --> WarmingWait: "command: skip"

    Phase2Stream --> WarmingWait: "EOF TCP DMDS / totalFrames alcanzado (Modelos 1 y 2A)"
    Phase2Stream --> WarmingWait: "TCP select > 3500 ms O silencio UDP > 2500 ms"
    Phase2Stream --> WarmingWait: "Tiempo > maxStreamCeilingMs (Techo de seguridad)"
    Phase2Stream --> Phase1Card: "Elemento N+1 ya listo"

    Phase1Card --> QuiescentStop: "deactivate() / Preempcion WEBUI_SYNC"
    Phase2Stream --> QuiescentStop: "deactivate() / Preempcion WEBUI_SYNC"
    SkipZeroMs --> [*]
    QuiescentStop --> [*]
```

### Detalle de Cada Estado y Recuperación ante Fallos

1. **`ARRANQUE Y DESCUBRIMIENTO mDNS / MQTT ACOTADO`**:
   - Core 1 muestra de inmediato una animación pixel-art procedimental a 60 FPS mientras Core 0 conecta al Wi-Fi, resuelve `dmdmediashowcase.local` (`224.0.0.251:5353`) y abre sesión MQTT `:1883` con **Last Will and Testament (`LWT`)** `{"online": false}` en `dmd_showcase/clients/{dmd_id}/status`.
2. **`PREPARACIÓN / ESPERA DE CONTENIDO` (`ready: false`)**:
   - **Receptores Dedicados / Pasivos (Modelos 2A y 2B)**: Mantienen su animación fluida a 60 FPS sin pantalla negra mientras el servidor descarga y transcodifica el primer título (`pkg_<domain>_<12hex>`).
   - **DMDs Multimotores (`ArcadeMatrix` — Modelo 1)**: Cuando hay más de un motor en rotación (`m_rotationSlotCount > 1`) y el servidor informa `{"ready": false}`, `MediaShowcaseBase` activa `m_skipTurnImmediately = true` (`isFinished() == true` en **`0 ms`**), saltando de inmediato al reloj o al clima sin congelar jamás el panel.
3. **`FASE 1 — CLAIM MQTT ACOTADO Y PIPELINING N+1`**:
   - En cuanto se reclama el elemento `N` (`pkg_<domain>_<12hex>`) mediante `start_card`, Core 0 **se desconecta de inmediato y cierra el socket TCP MQTT**, dejando **`0` sockets abiertos** durante los `5–12 s` del desplazamiento local a 60 FPS para preservar el máximo de SRAM interna.
   - En el servidor, `start_card` registra `N` en `./data/seen_history.json` (salvo cuando `mark_seen: false` proviene de `FrontendSync`), cambia `ready` a `{"ready": false}` y lanza **al instante la preparación en segundo plano del slot `N+1`**.
4. **`FASE 2 — STREAM HTTP TCP DMDS CON MARCAPASOS (DEFECTO) O UDP PASIVO DMDF`**:
   - **Modelos 1 y 2A (`MQTT+TCP` por defecto)**: Core 0 abre un `lwip_socket` puro (`HTTP/1.1`, `Connection: close`, `TCP_NODELAY`) hacia `:8890` solicitando `GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true&paced=1`. Omitir `drop_late=1` preserva `Content-Length` para que Uvicorn envíe **binario `DMDS` 100 % continuo sin `chunked`** (`0` cabeceras HTTP `chunked`), complementado en `ArcadeMatrix` con un `HttpChunkedDecoder` cero-heap de respaldo. El servidor marca el ritmo de cada fotograma (`X-DMD-Paced: 1`) mientras `lwip_select` aplica un **watchdog de inactividad TCP de `3.500 ms`** (rebanadas de `25 ms` para salir en `< 250 ms` ante `deactivate()`). Al llegar a `EOF` (o cerrar el socket), el bloque `finally` del servidor purga el paquete `N` del disco de inmediato.
   - **Modelo 2B (`UDP-PASSIVE :4210`, `supports_local_card: false`)**: `FfmpegUdpClockStreamer` envía fragmentos UDP `DMDF` (`<= 1408 B`) hacia `:4210`, protegidos por un **watchdog local de inactividad UDP de `2.500 ms`** (`millis() - lastUdpPacketMs > 2500`) y un techo de duración máxima.
5. **`DESACTIVACIÓN, PREEMPCIÓN WEBUI_SYNC Y RECUPERACIÓN TRAS REINICIO`**:
   - **Desactivación Quiescente (`< 250 ms`)**: Antes de liberar `m_frame565`, el motor marca `m_wantActive = false`; `lwip_select` / `SO_RCVTIMEO` despierta en `25–250 ms`, cierra el socket TCP `DMDS` (disparando la limpieza inmediata en el servidor), une la tarea de Core 0 (`stopWorkerTaskBounded()` con cero asignaciones dinámicas) y libera los búferes SRAM de forma segura (incluida la preempción `WEBUI_SYNC` en `ESP32_STD`).
   - **Reinicio Abrupto del ESP32**: El `RST`/timeout TCP en `:8890` o el `LWT` MQTT (`{"online": false}`) detienen al instante cualquier transmisión activa sin cancelar la preparación en curso de `N+1`.

---

## 4. Implementaciones C++ ESP32 de Referencia (`MQTT+TCP` por defecto y UDP Pasivo `DMDF`)

### A. Modelos 1 y 2A — Sesión MQTT Acotada Cero-Heap + Streamer TCP `DMDS` Puro LwIP (`MQTT+TCP` por defecto)

Esta implementación C++ muestra el patrón exacto cero-heap **`MQTT acotado (:1883) + HTTP/1.1 TCP DMDS puro LwIP (:8890)`** empleado en producción en [`ArcadeMatrix`](https://github.com/red77290/ArcadeMatrix/blob/main/src/engines/MediaShowcaseEngines.cpp) tanto para el **Modelo 1** (`config_source: "client"`) como para el **Modelo 2A** (`config_source: "server"`, `supports_local_card: true`):

1. **Claim MQTT Acotado Pre-Stream (`0` Sockets Abiertos Durante la Ficha)**: Core 0 conecta a MQTT `:1883`, publica `config` (Modelo 1) o `hello` (`supports_local_card: true`, Modelo 2A), extrae la ficha `ready` retenida (`pkg_<domain>_<12hex>`), publica `{"action":"start_card","item_id":"pkg_..."}` y **cierra inmediatamente el socket TCP MQTT**.
2. **Stream TCP `DMDS` Puro LwIP Cero-Heap (`?purge=true&paced=1`)**: Al finalizar el desplazamiento local a 60 FPS, Core 0 abre un `lwip_socket` (`TCP_NODELAY`, `SO_RCVTIMEO` de `250 ms`) y lee fotogramas RGB565LE sincronizados por el servidor (`X-DMD-Paced: 1`, `Content-Length` sin `chunked`) directamente en `g_frame565` con transferencia sin tearing **`g_frameSeq` Par/Impar + `g_consumedFrameSeq`** hacia Core 1 (`canvas_single`).

```cpp
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <lwip/sockets.h>
#include <lwip/netdb.h>
#include <atomic>
#include <cstring>

static constexpr uint16_t PANEL_W     = 128;
static constexpr uint16_t PANEL_H     = 32;
static constexpr size_t   FRAME_BYTES = static_cast<size_t>(PANEL_W) * PANEL_H * 2U; // 8,192 B

// Pre-allocated buffers (zero heap churn during Card scroll or TCP DMDS streaming)
static uint16_t* g_frame565       = nullptr; // Single 8 KB RGB565 frame buffer (Models 1 & 2A)
static uint8_t   g_ioScratch[1472]{};        // Fixed HTTP header / MQTT scratch buffer
static char      g_itemId[28]{};             // Compact "pkg_<domain>_<12hex>" (<= 24 chars)

// Lock-free tear-free handoff between Core 0 (LwIP TCP reader) and Core 1 (60 Hz HUB75)
static std::atomic<uint32_t> g_frameSeq{0};         // EVEN = coherent frame ready, ODD = Core 0 writing
static std::atomic<uint32_t> g_consumedFrameSeq{0}; // Last EVEN frameSeq presented by Core 1
static std::atomic<bool>     g_wantActive{true};    // Cleared on deactivate() / WEBUI_SYNC / skip
static std::atomic<bool>     g_cardReady{false};

// -----------------------------------------------------------------------------------------
// STEP 1 (CORE 0): SCOPED MQTT SESSION -> CLAIM CARD -> CLOSE SOCKET IMMEDIATELY!
// -----------------------------------------------------------------------------------------
bool claimReadyCardScopedMqtt(const char* host, const char* dmdId, const char* domain, bool isModel2A) {
    WiFiClient tcpMqtt;
    PubSubClient mqtt(tcpMqtt);
    mqtt.setServer(host, 1883);
    mqtt.setBufferSize(4096); // Needed only for this brief scoped session (freed on return!)

    g_cardReady.store(false, std::memory_order_relaxed);
    mqtt.setCallback([](char* /*topic*/, byte* payload, unsigned int len) {
        // Zero-copy extract compact item_id ("pkg_<domain>_<12hex>") when "ready":true
        if (len < 16 || memmem(payload, len, "\"ready\":true", 12) == nullptr &&
                        memmem(payload, len, "\"ready\": true", 13) == nullptr) {
            return;
        }
        const char* idKey = static_cast<const char*>(memmem(payload, len, "\"item_id\":\"", 11));
        if (idKey) {
            idKey += 11;
            const char* endQ = static_cast<const char*>(memchr(idKey, '"', 27));
            if (endQ) {
                const size_t n = static_cast<size_t>(endQ - idKey);
                memcpy(g_itemId, idKey, n);
                g_itemId[n] = '\0';
                // Decode Base64 mini-poster & localized synopsis into card state here...
                g_cardReady.store(true, std::memory_order_release);
            }
        }
    });

    char lwtTopic[96], topic[128], msg[256];
    snprintf(lwtTopic, sizeof(lwtTopic), "dmd_showcase/clients/%s/status", dmdId);
    if (!mqtt.connect(dmdId, nullptr, nullptr, lwtTopic, 0, true, "{\"online\":false}")) {
        return false;
    }

    const IPAddress ip = WiFi.localIP();
    if (isModel2A) {
        // Model 2A: Server-managed playlists in ./data/dmd_profiles.json, local 60 FPS Card on ESP32
        snprintf(topic, sizeof(topic), "dmd_showcase/clients/%s/hello", dmdId);
        snprintf(msg, sizeof(msg),
            "{\"dmd_id\":\"%s\",\"config_source\":\"server\",\"supports_local_card\":true,"
            "\"transport\":\"mqtt_tcp\",\"width\":%u,\"height\":%u,\"client_ip\":\"%u.%u.%u.%u\",\"lang\":\"es\"}",
            dmdId, PANEL_W, PANEL_H, ip[0], ip[1], ip[2], ip[3]);
    } else {
        // Model 1: Client-driven per-domain config
        snprintf(topic, sizeof(topic), "dmd_showcase/clients/%s/config", dmdId);
        snprintf(msg, sizeof(msg),
            "{\"dmd_id\":\"%s\",\"config_source\":\"client\",\"transport\":\"mqtt_tcp\","
            "\"width\":%u,\"height\":%u,\"client_ip\":\"%u.%u.%u.%u\",\"lang\":\"es\"}",
            dmdId, PANEL_W, PANEL_H, ip[0], ip[1], ip[2], ip[3]);
    }
    mqtt.publish(topic, reinterpret_cast<const uint8_t*>(msg), strlen(msg), /*retained=*/true);

    snprintf(topic, sizeof(topic), "dmd_showcase/clients/%s/%s/ready", dmdId, domain);
    mqtt.subscribe(topic);

    const uint32_t waitStart = millis();
    while (g_wantActive.load(std::memory_order_relaxed) && !g_cardReady.load(std::memory_order_acquire) &&
           (millis() - waitStart) < 600U) {
        mqtt.loop();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (!g_cardReady.load(std::memory_order_acquire)) {
        mqtt.disconnect(); // Multi-engine DMD yields turn in 0 ms when ready == false!
        return false;
    }

    // Claim Item N and trigger background N+1 preparation on the server, then CLOSE MQTT socket!
    mqtt.unsubscribe(topic);
    snprintf(topic, sizeof(topic), "dmd_showcase/clients/%s/%s/events", dmdId, domain);
    snprintf(msg, sizeof(msg),
        "{\"action\":\"start_card\",\"item_id\":\"%s\",\"client_ip\":\"%u.%u.%u.%u\"}",
        g_itemId, ip[0], ip[1], ip[2], ip[3]);
    mqtt.publish(topic, msg, /*retained=*/false);
    mqtt.loop();
    mqtt.disconnect();
    tcpMqtt.stop(); // <--- 0 sockets open during Phase 1 local 60 FPS Card scroll!
    return true;
}

// -----------------------------------------------------------------------------------------
// STEP 2 (CORE 0): ZERO-HEAP RAW LWIP HTTP/1.1 TCP `DMDS` STREAMER (?purge=true&paced=1)
// -----------------------------------------------------------------------------------------
static int recvExactBounded(int sock, uint8_t* dst, size_t n, uint32_t idleTimeoutMs) {
    size_t got = 0;
    uint32_t lastProgressMs = millis();
    while (got < n && g_wantActive.load(std::memory_order_relaxed)) {
        const ssize_t r = lwip_recv(sock, dst + got, n - got, 0);
        if (r > 0) {
            got += static_cast<size_t>(r);
            lastProgressMs = millis();
        } else if (r == 0) {
            return 0; // Clean server EOF
        } else {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) return -1;
            if ((millis() - lastProgressMs) > idleTimeoutMs) return -1;
        }
    }
    return (got == n) ? static_cast<int>(got) : -1;
}

bool streamUnifiedRgb565Http(const char* host, uint16_t port, const char* domain, const char* itemId) {
    IPAddress srvIp;
    if (!WiFi.hostByName(host, srvIp)) return false;

    const int sock = lwip_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock < 0) return false;

    int one = 1;
    lwip_setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    struct timeval tv{0, 250000}; // 250 ms slice -> deactivate() always exits in < 250 ms!
    lwip_setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    lwip_setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    addr.sin_addr.s_addr = static_cast<uint32_t>(srvIp);
    if (lwip_connect(sock, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) != 0) {
        lwip_close(sock);
        return false;
    }

    // Omit drop_late=1 so Content-Length is preserved and Uvicorn streams 100% raw unchunked binary DMDS
    const int reqLen = snprintf(
        reinterpret_cast<char*>(g_ioScratch), sizeof(g_ioScratch),
        "GET /api/v1/%s/stream/%s.rgb565.stream?purge=true&paced=1 HTTP/1.1\r\n"
        "Host: %s:%u\r\nConnection: close\r\n\r\n",
        domain, itemId, host, port);
    if (lwip_send(sock, g_ioScratch, static_cast<size_t>(reqLen), 0) != reqLen) {
        lwip_close(sock);
        return false;
    }

    // Read HTTP response headers into g_ioScratch and locate "\r\n\r\n"
    size_t used = 0, bodyOff = 0;
    while (used + 1U < sizeof(g_ioScratch) && g_wantActive.load(std::memory_order_relaxed)) {
        const ssize_t r = lwip_recv(sock, g_ioScratch + used, sizeof(g_ioScratch) - 1U - used, 0);
        if (r <= 0) {
            if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            lwip_close(sock);
            return false;
        }
        used += static_cast<size_t>(r);
        g_ioScratch[used] = '\0';
        const char* hdrEnd = strstr(reinterpret_cast<const char*>(g_ioScratch), "\r\n\r\n");
        if (hdrEnd) {
            bodyOff = static_cast<size_t>((hdrEnd + 4) - reinterpret_cast<const char*>(g_ioScratch));
            break;
        }
    }
    if (bodyOff == 0 || memmem(g_ioScratch, bodyOff, "200", 3) == nullptr) {
        lwip_close(sock);
        return false;
    }
    const bool serverPaced = (memmem(g_ioScratch, bodyOff, "X-DMD-Paced: 1", 14) != nullptr) ||
                             (memmem(g_ioScratch, bodyOff, "x-dmd-paced: 1", 14) != nullptr);

    // Read 16-byte Little-Endian `<4sBBHHHHH` DMDS header
    uint8_t dmdsHdr[16]{};
    const size_t avail = used - bodyOff;
    const size_t take  = (avail < 16U) ? avail : 16U;
    if (take > 0) memcpy(dmdsHdr, g_ioScratch + bodyOff, take);
    if (take < 16U && recvExactBounded(sock, dmdsHdr + take, 16U - take, 3500U) != static_cast<int>(16U - take)) {
        lwip_close(sock);
        return false;
    }
    if (memcmp(dmdsHdr, "DMDS", 4) != 0) {
        lwip_close(sock);
        return false;
    }
    const uint8_t  fps         = (dmdsHdr[5] > 0U) ? dmdsHdr[5] : 20U;
    const uint16_t w           = static_cast<uint16_t>(dmdsHdr[6]  | (dmdsHdr[7]  << 8));
    const uint16_t h           = static_cast<uint16_t>(dmdsHdr[8]  | (dmdsHdr[9]  << 8));
    const uint16_t totalFrames = static_cast<uint16_t>(dmdsHdr[10] | (dmdsHdr[11] << 8));
    if (w != PANEL_W || h != PANEL_H || totalFrames == 0U) {
        lwip_close(sock);
        return false;
    }

    size_t leftover = (avail > 16U) ? (avail - 16U) : 0U;
    size_t leftoverOff = bodyOff + 16U;

    for (uint16_t f = 0; f < totalFrames && g_wantActive.load(std::memory_order_relaxed); ++f) {
        // 1. Wait for next server-paced frame in lwip_select WHILE g_frameSeq stays EVEN
        //    so Core 1 never stalls during 40-66 ms inter-frame intervals!
        if (leftover == 0U) {
            const uint32_t waitStart = millis();
            bool readable = false;
            while (g_wantActive.load(std::memory_order_relaxed)) {
                fd_set rfds;
                FD_ZERO(&rfds);
                FD_SET(sock, &rfds);
                struct timeval selTv{0, 25000}; // 25 ms slice
                const int sr = lwip_select(sock + 1, &rfds, nullptr, nullptr, &selTv);
                if (sr > 0) { readable = true; break; }
                if (sr < 0 && errno != EINTR) break;
                if ((millis() - waitStart) > 3500U) break; // 3,500 ms TCP inactivity watchdog
            }
            if (!readable) break;
        }

        // 2. Tear-free single-buffer guard: let Core 1 finish reading prior EVEN frame (<= 18 ms)
        const uint32_t curSeq = g_frameSeq.load(std::memory_order_acquire);
        if (curSeq > 0U && g_consumedFrameSeq.load(std::memory_order_acquire) != curSeq) {
            const uint32_t guardStart = millis();
            while (g_wantActive.load(std::memory_order_relaxed) &&
                   g_consumedFrameSeq.load(std::memory_order_acquire) != curSeq &&
                   (millis() - guardStart) < 18U) {
                vTaskDelay(pdMS_TO_TICKS(1));
            }
        }

        // 3. Mark g_frameSeq ODD -> read FRAME_BYTES straight into g_frame565 (~1.2 ms) -> mark EVEN
        g_frameSeq.fetch_add(1U, std::memory_order_acq_rel); // ODD (write in progress)
        uint8_t* dst = reinterpret_cast<uint8_t*>(g_frame565);
        size_t copied = 0U;
        if (leftover > 0U) {
            copied = (leftover < FRAME_BYTES) ? leftover : FRAME_BYTES;
            memcpy(dst, g_ioScratch + leftoverOff, copied);
            leftover -= copied;
            leftoverOff += copied;
        }
        const int r = (copied < FRAME_BYTES)
                          ? recvExactBounded(sock, dst + copied, FRAME_BYTES - copied, 3500U)
                          : 1;
        g_frameSeq.fetch_add(1U, std::memory_order_release); // EVEN (coherent frame ready!)
        if (r <= 0) break; // Clean server EOF or socket timeout

        // With X-DMD-Paced: 1, server clocks frames monotonically -> zero client sleep needed!
        if (!serverPaced) {
            vTaskDelay(pdMS_TO_TICKS(1000U / fps));
        } else if ((f & 3U) == 3U) {
            taskYIELD();
        }
    }

    lwip_close(sock); // Closing TCP socket triggers immediate server-side purge_consumed_package()!
    return true;
}

// -----------------------------------------------------------------------------------------
// STEP 3 (CORE 1): ZERO-ALLOCATION 60 HZ `canvas_single` DIRTY-GATED COMMIT
// -----------------------------------------------------------------------------------------
void renderStreamFrameOnCore1(uint16_t* offscreenCanvas565, uint32_t& lastRenderedSeq) {
    const uint32_t fSeq = g_frameSeq.load(std::memory_order_acquire);
    if ((fSeq & 1U) == 0U && fSeq != 0U && fSeq != lastRenderedSeq) {
        memcpy(offscreenCanvas565, g_frame565, FRAME_BYTES);
        if (g_frameSeq.load(std::memory_order_acquire) == fSeq) {
            lastRenderedSeq = fSeq;
            g_consumedFrameSeq.store(fSeq, std::memory_order_release);
            // Burst offscreenCanvas565 to Single HUB75 DMA Buffer via blitCanvas565()
        }
    }
}
```

> [!TIP]
> **Control en Vivo Modelo 2A (`dmd_showcase/clients/{dmd_id}/command`)**: Los paneles dedicados Modelo 2A que mantienen una escucha ligera MQTT en `command` gestionan `"skip"`, `"stop"`, `"pause"`, `"resume"` y `"play_card"` (`FrontendSync` / `play_now`) poniendo `g_wantActive = false`, lo que cierra el socket TCP `DMDS` en `< 25 ms` y pasa de inmediato a la nueva ficha.

---

### B. Modelo 2B — Firmware Monolítico ~180 Líneas UDP Pasivo `:4210` `DMDF` (`platformio.ini` + `src/main.cpp`)

Para montajes ultramínimos `supports_local_card: false` (`UDP-PASSIVE` `:4210`), este firmware C++ monolítico y autónomo implementa el pipeline **`Single DMA Buffer (`mxconfig.double_buff = false`) + Lienzo Fuera de Pantalla `GFXcanvas16` + Codificador Bitplane Burst CIE 1931 (`blitCanvas565`) + Dirty-Frame Gating`** usando [`mrfaptastic/ESP32-HUB75-MatrixPanel-I2S-DMA`](https://github.com/mrfaptastic/ESP32-HUB75-MatrixPanel-I2S-DMA). Compila directamente en **ESP32 Clásico (`esp32dev`, sin PSRAM)** y **ESP32-S3 (`esp32-s3-devkitc-1` / Waveshare RGB Matrix con PSRAM)**:

#### `platformio.ini`

```ini
[platformio]
default_envs = esp32dev

[env]
platform = espressif32
framework = arduino
monitor_speed = 115200
lib_deps =
    mrfaptastic/ESP32 HUB75 LED MATRIX PANEL DMA Display @ ^3.0.11
    adafruit/Adafruit GFX Library @ ^1.11.9

; Target 1: Standard ESP32 (WROOM-32 / DevKitC without PSRAM)
[env:esp32dev]
board = esp32dev
build_flags =
    -D CORE_DEBUG_LEVEL=1
    -O2

; Target 2: ESP32-S3 with Octal PSRAM (e.g. Waveshare ESP32-S3-RGB-Matrix / N16R8)
[env:esp32s3]
board = esp32-s3-devkitc-1
board_build.arduino.memory_type = qio_opi
build_flags =
    -D BOARD_HAS_PSRAM
    -D SPIRAM_DMA_BUFFER
    -D CORE_DEBUG_LEVEL=1
    -O2
```

#### `src/main.cpp` (Implementación Monolítica Completa)

```cpp
/**
 * @file main.cpp
 * @brief Monolithic ESP32 / ESP32-S3 HUB75 Receiver for DMD Media Showcase (Mode 2B)
 *        using the "Single DMA Buffer + Offscreen RGB565 Canvas" (`canvas_single`) pipeline.
 *
 * Architectural Highlights (Proven in Production on ArcadeMatrix):
 *  1. Single HUB75 DMA Buffer (`mxconfig.double_buff = false`):
 *     Cuts scarce internal DMA-capable SRAM (`MALLOC_CAP_DMA`) usage by 50% compared to
 *     DMA double-buffering, leaving ample contiguous heap for Wi-Fi + LwIP + UDP on
 *     standard ESP32 boards without PSRAM (`ESP32_STD`).
 *  2. Offscreen Linear RGB565 Canvas (`Adafruit_GFX` / `GFXcanvas16`):
 *     All drawing (idle 60 FPS procedural animation, local HUD overlays, or incoming UDP
 *     frames) happens exclusively in offscreen memory (`2 bytes/pixel`). `clearScreen()` is
 *     NEVER called on the live DMA scanout buffer, eliminating black-frame strobe/flicker.
 *  3. In-Place Multiplexed BCM Bitplane Burst Encoder (`blitCanvas565`):
 *     Encodes upper `(y)` and lower `(y + H/2)` HUB75 scan halves simultaneously with
 *     perceptual CIE 1931 gamma LUTs directly into `frame_buffer[0]` (`~300 us` for 128x32),
 *     bypassing thousands of per-pixel virtual `drawPixel()` calls.
 *  4. Dirty-Frame Gating (`s_completedUdpSeq != s_lastPresentedUdpSeq`):
 *     Core 1 only bursts the offscreen canvas into DMA memory when a brand-new frame is
 *     actually ready, preventing redundant bus contention while I2S scans the panel.
 *  5. Strict Dual-Core Isolation:
 *     - Core 0 (`PRO_CPU`): Wi-Fi, mDNS, periodic HTTP `hello`, lock-free UDP `DMDF` assembly.
 *     - Core 1 (`APP_CPU`): Zero-allocation, zero-mutex 60 Hz canvas composition & DMA commit.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <esp_heap_caps.h>
#include <Adafruit_GFX.h>
#include <atomic>
#include <cstring>
#include <memory>
#include <vector>

#if defined(SPIRAM_DMA_BUFFER)
#include "rom/cache.h"
#endif

// Expose `MatrixPanel_I2S_DMA` internal `frame_buffer` row-plane pointers to our subclass
// for direct in-place BCM bitplane burst encoding (must come AFTER STL headers).
#define private protected
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#undef private

// =========================================================================================
// STEP 1: PANEL GEOMETRY, NETWORK CONFIGURATION & HARDWARE PIN PROFILES
// =========================================================================================

static constexpr uint16_t MODULE_W     = 64;   // Single HUB75 panel width (typically 64)
static constexpr uint16_t MODULE_H     = 32;   // Single HUB75 panel height (32 or 64)
static constexpr uint16_t CHAIN_LEN    = 2;    // Number of chained panels (2 x 64x32 = 128x32)
static constexpr uint16_t PANEL_W      = MODULE_W * CHAIN_LEN;
static constexpr uint16_t PANEL_H      = MODULE_H;
static constexpr size_t   FRAME_PIXELS = static_cast<size_t>(PANEL_W) * PANEL_H;
static constexpr size_t   FRAME_BYTES  = FRAME_PIXELS * sizeof(uint16_t); // 8,192 B for 128x32

static constexpr uint16_t UDP_PORT          = 4210;
static constexpr uint32_t HELLO_INTERVAL_MS = 15000U;
static constexpr uint32_t UDP_WATCHDOG_MS   = 2500U;

static const char* WIFI_SSID    = "YOUR_WIFI_SSID";
static const char* WIFI_PASS    = "YOUR_WIFI_PASSWORD";
static const char* DMD_ID       = "esp32_passive_128x32";
static const char* CONTENT_LANG = "es"; // Broadcast language ("fr", "en", "es", "de", "it", "pt", "ja")
static const char* SHOWCASE_HELLO_URL = "http://dmdmediashowcase.local:8890/api/v1/clients/hello";

// Classic ESP32 (Xtensa LX6) I2S TX FIFO transmits 16-bit words swapped within each 32-bit pair.
// ESP32-S3 LCD_CAM transmits 16-bit words in natural sequential order.
#ifndef MATRIX_TX_ADJUST
#if defined(CONFIG_IDF_TARGET_ESP32) && !defined(CONFIG_IDF_TARGET_ESP32S3)
#define MATRIX_TX_ADJUST(x) (((x) & 1U) ? ((x) - 1U) : ((x) + 1U))
#else
#define MATRIX_TX_ADJUST(x) (x)
#endif
#endif

// Default GPIO mapping per target architecture (adjust to match your shield/wiring).
// Note: On ESP32-S3 with Octal PSRAM, NEVER assign GPIO 32..37 to HUB75!
static void populateHub75Pins(HUB75_I2S_CFG::i2s_pins& pins) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    // Waveshare ESP32-S3-RGB-Matrix / Generic ESP32-S3 PSRAM-safe pinout
    pins.r1 = 4;  pins.g1 = 5;  pins.b1 = 6;
    pins.r2 = 7;  pins.g2 = 15; pins.b2 = 16;
    pins.a  = 18; pins.b  = 8;  pins.c  = 3;
    pins.d  = 42; pins.e  = (PANEL_H >= 64) ? 9 : -1;
    pins.lat = 40; pins.oe = 2; pins.clk = 41;
#else
    // Standard ESP32 (WROOM-32 / DevKitC) HUB75 pinout
    pins.r1 = 25; pins.g1 = 26; pins.b1 = 27;
    pins.r2 = 14; pins.g2 = 12; pins.b2 = 13;
    pins.a  = 33; pins.b  = 32; pins.c  = 22;
    pins.d  = 17; pins.e  = (PANEL_H >= 64) ? 21 : -1;
    pins.lat = 4; pins.oe = 15; pins.clk = 16;
#endif
}

// =========================================================================================
// STEP 2: SINGLE-DMA-BUFFER PANEL SUBCLASS WITH IN-PLACE CIE 1931 BITPLANE BURST ENCODER
// =========================================================================================

// Preserves HUB75 control bits (LAT/OE/address in bits 6..15) while clearing R1G1B1R2G2B2 (bits 0..5).
static constexpr uint16_t HUB75_RGB12_CLEAR_MASK = 0b1111111111000000U;

// Perceptual CIE 1931 lightness curve (8-bit, gamma ~2.2) for silky dark gradients on LEDs.
static const uint8_t kCie1931Lut8[256] = {
      0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   1,   1,   1,   1,   1,   1,
      1,   1,   2,   2,   2,   2,   2,   2,   3,   3,   3,   3,   4,   4,   4,   4,
      5,   5,   5,   5,   6,   6,   6,   7,   7,   7,   8,   8,   8,   9,   9,  10,
     10,  10,  11,  11,  12,  12,  13,  13,  14,  14,  15,  15,  16,  16,  17,  18,
     18,  19,  19,  20,  21,  21,  22,  23,  23,  24,  25,  25,  26,  27,  28,  28,
     29,  30,  31,  32,  32,  33,  34,  35,  36,  37,  38,  38,  39,  40,  41,  42,
     43,  44,  45,  46,  47,  48,  49,  51,  52,  53,  54,  55,  56,  57,  58,  60,
     61,  62,  63,  65,  66,  67,  68,  70,  71,  72,  74,  75,  77,  78,  79,  81,
     82,  84,  85,  87,  88,  90,  91,  93,  94,  96,  98,  99, 101, 103, 104, 106,
    108, 110, 111, 113, 115, 117, 119, 120, 122, 124, 126, 128, 130, 132, 134, 136,
    138, 140, 142, 144, 146, 148, 150, 153, 155, 157, 159, 162, 164, 166, 168, 171,
    173, 175, 178, 180, 183, 185, 188, 190, 193, 195, 198, 201, 203, 206, 208, 211,
    214, 216, 219, 222, 225, 227, 230, 233, 236, 239, 242, 245, 247, 250, 253, 255,
    255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
    255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255,
    255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255
};

class SingleBufferCanvasPanel : public MatrixPanel_I2S_DMA {
public:
    using MatrixPanel_I2S_DMA::MatrixPanel_I2S_DMA;

    // Precompute channel LUTs scaled to the configured BCM bit depth (e.g. 6-bit on ESP32_STD, 8-bit on S3).
    void initGammaLuts(uint8_t depth) {
        if (depth < 2) depth = 2;
        if (depth > 8) depth = 8;
        m_depth = depth;
        const uint8_t  shift  = static_cast<uint8_t>(8U - depth);
        const uint16_t round  = (shift > 0U) ? static_cast<uint16_t>(1U << (shift - 1U)) : 0U;
        const uint16_t maxVal = static_cast<uint16_t>((1U << depth) - 1U);

        for (uint16_t r = 0; r < 32; ++r) {
            uint8_t r8 = static_cast<uint8_t>((r << 3) | (r >> 2));
            uint16_t v = static_cast<uint16_t>((kCie1931Lut8[r8] + round) >> shift);
            m_lutR[r]  = static_cast<uint8_t>((v > maxVal) ? maxVal : v);
        }
        for (uint16_t g = 0; g < 64; ++g) {
            uint8_t g8 = static_cast<uint8_t>((g << 2) | (g >> 4));
            uint16_t v = static_cast<uint16_t>((kCie1931Lut8[g8] + round) >> shift);
            m_lutG[g]  = static_cast<uint8_t>((v > maxVal) ? maxVal : v);
        }
        for (uint16_t b = 0; b < 32; ++b) {
            uint8_t b8 = static_cast<uint8_t>((b << 3) | (b >> 2));
            uint16_t v = static_cast<uint16_t>((kCie1931Lut8[b8] + round) >> shift);
            m_lutB[b]  = static_cast<uint8_t>((v > maxVal) ? maxVal : v);
        }
    }

    // Burst-encode a linear RGB565 offscreen canvas directly into the single active DMA buffer (`frame_buffer[0]`).
    // Because every bitplane word is overwritten in-place without clearing first, there is zero black strobe.
    void blitCanvas565(const uint16_t* canvas, uint16_t canvasW, uint16_t canvasH) {
        if (!canvas || !initialized) return;
        const uint16_t w   = static_cast<uint16_t>(PIXELS_PER_ROW);
        const uint16_t rpf = static_cast<uint16_t>(ROWS_PER_FRAME); // PANEL_H / 2 (1/16 or 1/32 scan)
        if (canvasW != w || canvasH != static_cast<uint16_t>(m_cfg.mx_height)) return;

        auto& dmaFb = frame_buffer[0]; // Single DMA buffer (`double_buff == false`)
        if (dmaFb.rowBits.size() < rpf) return;

        for (uint16_t y = 0; y < rpf; ++y) {
            const uint16_t* upperRow = canvas + (static_cast<size_t>(y) * w);
            const uint16_t* lowerRow = canvas + (static_cast<size_t>(y + rpf) * w);

            for (uint8_t p = 0; p < m_depth; ++p) {
                uint16_t* dmaRow = dmaFb.rowBits[y]->getDataPtr(p);
                if (!dmaRow) continue;

                for (uint16_t x = 0; x < w; ++x) {
                    const uint16_t c1 = upperRow[x];
                    const uint16_t c2 = lowerRow[x];

                    const uint16_t r1 = (m_lutR[(c1 >> 11) & 0x1FU] >> p) & 1U;
                    const uint16_t g1 = (m_lutG[(c1 >> 5)  & 0x3FU] >> p) & 1U;
                    const uint16_t b1 = (m_lutB[ c1        & 0x1FU] >> p) & 1U;

                    const uint16_t r2 = (m_lutR[(c2 >> 11) & 0x1FU] >> p) & 1U;
                    const uint16_t g2 = (m_lutG[(c2 >> 5)  & 0x3FU] >> p) & 1U;
                    const uint16_t b2 = (m_lutB[ c2        & 0x1FU] >> p) & 1U;

                    const uint16_t rgb12 = static_cast<uint16_t>(
                        r1 | (g1 << 1) | (b1 << 2) | (r2 << 3) | (g2 << 4) | (b2 << 5)
                    );
                    const uint16_t ax = MATRIX_TX_ADJUST(x);
                    dmaRow[ax] = static_cast<uint16_t>((dmaRow[ax] & HUB75_RGB12_CLEAR_MASK) | rgb12);
                }

#if defined(SPIRAM_DMA_BUFFER)
                // Flush CPU dcache line to SPIRAM so LCD_CAM DMA observes updated bitplanes immediately.
                Cache_WriteBack_Addr(reinterpret_cast<uint32_t>(dmaRow), static_cast<uint32_t>(w * sizeof(uint16_t)));
#endif
            }
        }
    }

private:
    uint8_t m_depth = 6;
    uint8_t m_lutR[32]{};
    uint8_t m_lutG[64]{};
    uint8_t m_lutB[32]{};
};

// =========================================================================================
// STEP 3: LOCK-FREE DUAL-CORE BUFFERS (UDP STAGING ON CORE 0 -> OFFSCREEN CANVAS ON CORE 1)
// =========================================================================================

static SingleBufferCanvasPanel* g_panel  = nullptr;
static GFXcanvas16*             g_canvas = nullptr; // Core 1 private offscreen canvas (full Adafruit_GFX API)

// Ping-pong RGB565 buffers for tear-free UDP `DMDF` packet reassembly on Core 0.
// Allocated in PSRAM when present, otherwise in internal SRAM (`2 x 8 KB = 16 KB` for 128x32).
static uint16_t*             g_udpSlots[2]        = {nullptr, nullptr};
static std::atomic<uint8_t>  s_frontUdpIdx{0};
static std::atomic<uint32_t> s_completedUdpSeq{0};
static std::atomic<uint32_t> s_lastUdpPacketMs{0};
static std::atomic<bool>     s_streamActive{false};

static uint16_t* allocRgb565Buffer(size_t bytes) {
    uint16_t* buf = nullptr;
    if (psramFound()) {
        buf = static_cast<uint16_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    }
    if (!buf) {
        buf = static_cast<uint16_t*>(heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    }
    if (buf) {
        memset(buf, 0, bytes);
    }
    return buf;
}

// =========================================================================================
// STEP 4: CORE 0 NETWORK WORKER (WIFI + HTTP HELLO + LOCK-FREE UDP `DMDF` REASSEMBLER)
// =========================================================================================

static void netWorkerTask(void*) {
    WiFi.mode(WIFI_STA);
    // CRITICAL: Disable Wi-Fi modem power-save sleep so 100 ms DTIM intervals never stall UDP packets!
    WiFi.setSleep(false);
#if defined(CONFIG_IDF_TARGET_ESP32) && !defined(CONFIG_IDF_TARGET_ESP32S3)
    // Cap RF TX bursts on classic ESP32 to prevent transient USB 5V rail voltage dips during HUB75 scanout.
    WiFi.setTxPower(WIFI_POWER_17dBm);
#endif
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) {
        vTaskDelay(pdMS_TO_TICKS(250));
    }
    MDNS.begin(DMD_ID);

    WiFiUDP udp;
    udp.begin(UDP_PORT);

    uint8_t  pkt[1440]; // 10-byte `<4sHBBH` header + up to 1408-byte RGB565LE slice (<= 1418 B < Wi-Fi MTU)
    uint8_t  backIdx      = 1;
    uint16_t curFrameSeq  = 0xFFFFU;
    uint64_t chunkMask    = 0ULL;
    uint32_t lastHelloMs  = 0U;

    for (;;) {
        const uint32_t now = millis();

        // 4.1 Periodic non-blocking HTTP Hello announcement (registers IP + geometry in `./data/dmd_profiles.json`).
        // Skip HTTP heartbeat while UDP packets are actively streaming so socket read latency stays < 1 ms.
        const bool activelyReceiving = s_streamActive.load(std::memory_order_relaxed) &&
                                       ((now - s_lastUdpPacketMs.load(std::memory_order_relaxed)) < 1200U);
        if (!activelyReceiving && WiFi.status() == WL_CONNECTED &&
            (lastHelloMs == 0U || (now - lastHelloMs) >= HELLO_INTERVAL_MS)) {
            lastHelloMs = now;
            HTTPClient http;
            http.setTimeout(1200);
            if (http.begin(SHOWCASE_HELLO_URL)) {
                http.addHeader("Content-Type", "application/json");
                char body[256];
                const IPAddress ip = WiFi.localIP();
                snprintf(
                    body, sizeof(body),
                    "{\"dmd_id\":\"%s\",\"config_source\":\"server\",\"supports_local_card\":false,"
                    "\"width\":%u,\"height\":%u,\"udp_ip\":\"%u.%u.%u.%u\",\"udp_port\":%u,\"lang\":\"%s\"}",
                    DMD_ID, PANEL_W, PANEL_H, ip[0], ip[1], ip[2], ip[3], UDP_PORT, CONTENT_LANG
                );
                http.POST(reinterpret_cast<uint8_t*>(body), strlen(body));
                http.end();
            }
        }

        // 4.2 Drain all pending UDP `DMDF` datagrams in a tight burst before yielding.
        int pktSize = udp.parsePacket();
        if (pktSize > 10 && pktSize <= static_cast<int>(sizeof(pkt))) {
            while (pktSize > 10 && pktSize <= static_cast<int>(sizeof(pkt))) {
                const int len = udp.read(pkt, sizeof(pkt));
                if (len > 10 && memcmp(pkt, "DMDF", 4) == 0) {
                    const uint16_t seq         = static_cast<uint16_t>(pkt[4] | (pkt[5] << 8));
                    const uint8_t  chunkIdx    = pkt[6];
                    const uint8_t  totalChunks = (pkt[7] > 0U) ? pkt[7] : 1U;
                    const uint16_t byteOff     = static_cast<uint16_t>(pkt[8] | (pkt[9] << 8));
                    const size_t   payloadLen  = static_cast<size_t>(len - 10);

                    if (seq != curFrameSeq) {
                        curFrameSeq = seq;
                        chunkMask   = 0ULL;
                    }

                    if (chunkIdx < totalChunks && totalChunks <= 64U && (byteOff + payloadLen) <= FRAME_BYTES) {
                        memcpy(reinterpret_cast<uint8_t*>(g_udpSlots[backIdx]) + byteOff, pkt + 10, payloadLen);
                        chunkMask |= (1ULL << chunkIdx);

                        const uint64_t expectedMask = (totalChunks == 64U) ? ~0ULL : ((1ULL << totalChunks) - 1ULL);
                        if (chunkMask == expectedMask) {
                            chunkMask = 0ULL;
                            // Publish completed frame lock-free to Core 1 and swap Core 0 staging slot.
                            const uint8_t prevFront = s_frontUdpIdx.load(std::memory_order_relaxed);
                            s_frontUdpIdx.store(backIdx, std::memory_order_release);
                            backIdx = prevFront;
                            s_completedUdpSeq.fetch_add(1U, std::memory_order_release);
                            s_streamActive.store(true, std::memory_order_release);
                            // Pre-seed next back buffer with the just-completed frame so any single lost
                            // UDP slice in a future frame preserves prior pixels instead of black tearing.
                            memcpy(g_udpSlots[backIdx], g_udpSlots[s_frontUdpIdx.load(std::memory_order_relaxed)], FRAME_BYTES);
                        }
                        s_lastUdpPacketMs.store(millis(), std::memory_order_release);
                    }
                }
                pktSize = udp.parsePacket();
            }
        } else {
            // 4.3 Local 2,500 ms UDP inactivity watchdog: smoothly return to idle animation on EOF or server reboot.
            const uint32_t lastPkt = s_lastUdpPacketMs.load(std::memory_order_acquire);
            if (s_streamActive.load(std::memory_order_relaxed) && (millis() - lastPkt) > UDP_WATCHDOG_MS) {
                s_streamActive.store(false, std::memory_order_release);
            }
            vTaskDelay(pdMS_TO_TICKS(1));
        }
    }
}

// =========================================================================================
// STEP 5: CORE 1 REAL-TIME 60 HZ RENDER LOOP (OFFSCREEN CANVAS -> DIRTY-GATED SINGLE DMA)
// =========================================================================================

// Non-blocking procedural waiting animation rendered strictly into the offscreen `GFXcanvas16`.
static void renderWaitingAnimationToCanvas(GFXcanvas16& canvas, uint32_t nowMs) {
    uint16_t* dst = canvas.getBuffer();
    const uint32_t t = nowMs >> 4;
    for (uint16_t y = 0; y < PANEL_H; ++y) {
        uint16_t* row = dst + (static_cast<size_t>(y) * PANEL_W);
        for (uint16_t x = 0; x < PANEL_W; ++x) {
            const uint8_t  v = static_cast<uint8_t>((x * 5U + y * 9U + t) & 0x3FU);
            const uint16_t r = static_cast<uint16_t>((v >> 2) & 0x1FU);
            const uint16_t g = static_cast<uint16_t>((v >> 1) & 0x3FU);
            const uint16_t b = static_cast<uint16_t>((v >> 1) & 0x1FU);
            row[x] = static_cast<uint16_t>((r << 11) | (g << 5) | b);
        }
    }
}

void setup() {
    Serial.begin(115200);

    // 5.1 Allocate offscreen `GFXcanvas16` + lock-free UDP staging buffers BEFORE Wi-Fi fragments heap.
    g_canvas      = new GFXcanvas16(PANEL_W, PANEL_H);
    g_udpSlots[0] = allocRgb565Buffer(FRAME_BYTES);
    g_udpSlots[1] = allocRgb565Buffer(FRAME_BYTES);
    if (!g_canvas || !g_canvas->getBuffer() || !g_udpSlots[0] || !g_udpSlots[1]) {
        Serial.println("[FATAL] Failed to allocate offscreen RGB565 buffers!");
        for (;;) { delay(1000); }
    }

    // 5.2 Configure HUB75 with SINGLE DMA BUFFER (`mxconfig.double_buff = false`).
    HUB75_I2S_CFG::i2s_pins pins{};
    populateHub75Pins(pins);
    HUB75_I2S_CFG mxconfig(MODULE_W, MODULE_H, CHAIN_LEN, pins);

    // 6-bit BCM depth (64 perceptual CIE levels/channel) fits comfortably in internal DMA SRAM on ESP32_STD;
    // upgrade automatically to 8-bit BCM depth when PSRAM is available (e.g. ESP32-S3).
    const uint8_t colorDepth = psramFound() ? 8U : 6U;
    mxconfig.setPixelColorDepthBits(colorDepth);
    mxconfig.double_buff      = false; // <--- CRITICAL: Single DMA Buffer saves 50% internal DMA DRAM!
    mxconfig.min_refresh_rate = 90;
    mxconfig.latch_blanking   = 4;
    mxconfig.clkphase         = false;
    mxconfig.driver           = HUB75_I2S_CFG::SHIFTREG; // Or FM6126A / ICN2038S depending on your LED panels

    g_panel = new SingleBufferCanvasPanel(mxconfig);
    if (!g_panel->begin()) {
        Serial.println("[FATAL] HUB75 Single-Buffer DMA allocation failed!");
        for (;;) { delay(1000); }
    }
    g_panel->initGammaLuts(colorDepth);
    g_panel->setBrightness8(96); // 0..255 safe cabinet brightness

    // Commit clean black Frame 0 once at boot via the offscreen canvas.
    g_canvas->fillScreen(0x0000);
    g_panel->blitCanvas565(g_canvas->getBuffer(), PANEL_W, PANEL_H);

    // 5.3 Launch network & UDP reassembly worker strictly pinned to Core 0 (`PRO_CPU`).
    xTaskCreatePinnedToCore(netWorkerTask, "ShowcaseUdpNet", 6144, nullptr, 1, nullptr, 0);
}

void loop() {
    // Runs on Core 1 (`APP_CPU`): strictly ZERO heap allocation and ZERO mutex!
    static uint32_t s_lastPresentedUdpSeq = 0U;
    static uint32_t s_lastFrameTickMs     = 0U;

    const uint32_t now = millis();
    if ((now - s_lastFrameTickMs) < 16U) { // Pace Core 1 composition loop at ~60 Hz (16.6 ms)
        vTaskDelay(pdMS_TO_TICKS(1));
        return;
    }
    s_lastFrameTickMs = now;

    bool canvasDirty = false;

    if (s_streamActive.load(std::memory_order_acquire)) {
        const uint32_t latestSeq = s_completedUdpSeq.load(std::memory_order_acquire);
        // Dirty-Frame Gating: ONLY touch the offscreen canvas & Single DMA Buffer when a new UDP frame arrived!
        if (latestSeq != s_lastPresentedUdpSeq) {
            const uint8_t frontIdx = s_frontUdpIdx.load(std::memory_order_acquire);
            memcpy(g_canvas->getBuffer(), g_udpSlots[frontIdx], FRAME_BYTES);

            // Optional hook for custom firmwares: draw local Adafruit_GFX HUD / score / clock overlays here
            // into `g_canvas` (e.g. `g_canvas->setCursor(...); g_canvas->print(...);`) before DMA burst!

            s_lastPresentedUdpSeq = latestSeq;
            canvasDirty = true;
        }
    } else {
        // Boot / Warming / Idle state: compose smooth 60 FPS animation in offscreen canvas.
        renderWaitingAnimationToCanvas(*g_canvas, now);
        canvasDirty = true;
    }

    // Commit offscreen canvas to Single HUB75 DMA Buffer ONLY when dirty (in-place burst, zero black clear).
    if (canvasDirty) {
        g_panel->blitCanvas565(g_canvas->getBuffer(), PANEL_W, PANEL_H);
    }
}
```

---

### C. Callback Opcional de Comandos en Vivo Modelo 2A (`dmd_showcase/clients/{dmd_id}/command`)

Cuando un panel dedicado Modelo 2A (`ESP32-S3` o `ESP32_STD` sin WebUI local) mantiene un suscriptor ligero MQTT abierto para recibir `Skip` / `Pause` / `Play Now` en `< 50 ms` mientras reproduce por TCP `DMDS`:

```cpp
void onHybridCommandMessage(const char* topic, const char* jsonPayload) {
    if (strstr(topic, "/command") == nullptr) return;
    // action: "play_card" | "skip" | "pause" | "resume" | "stop"
    if (strstr(jsonPayload, "\"skip\"") || strstr(jsonPayload, "\"stop\"")) {
        // Wakes lwip_select() within <= 25 ms -> closes TCP DMDS socket & purges item on server!
        g_wantActive.store(false, std::memory_order_release);
    } else if (strstr(jsonPayload, "\"pause\"")) {
        g_paused.store(true, std::memory_order_release);
    } else if (strstr(jsonPayload, "\"resume\"")) {
        g_paused.store(false, std::memory_order_release);
    } else if (strstr(jsonPayload, "\"play_card\"")) {
        // Immediate play_now preemption pushed by WebUI or FrontendSync
        g_wantActive.store(false, std::memory_order_release);
        parseCardIntoState(jsonPayload);
        g_preemptWithNewCard.store(true, std::memory_order_release);
    }
}
```

---

## 5. Referencia API REST y MQTT: Idioma (`lang`), Búsqueda Filtrada, Cola Bajo Demanda y `FrontendSync`

Todos los perfiles DMD gestionados en el servidor, controles en vivo, búsquedas multilingües e integraciones con frontends arcade (`FrontendSync`) se exponen mediante la API REST FastAPI (`:8890`) y se sincronizan por MQTT (`:1883`).

### 5.1 Búsqueda Filtrada, Referenciales y Cadenas de Fuentes Configurables (`GET /api/v1/{domain}/search`, `/filters`)

Consulta la cadena multifuente configurable (`source_policies` en `/api/v1/admin/config`: `IGDB v4`, `ScreenScraper`, `ArcadeItalia`, `Libretro/Wikidata` para `games`; `TMDB` + `iTunes` para `movies`; `TMDB` + `TheTVDB v4` + `TVMaze` para `series`; `Jikan/AnimeThemes` + `AniList` para `anime`) en el idioma de emisión solicitado (`lang`) con filtros opcionales.

- **Enriquecimiento entre Fuentes (`enrich_missing`) y Procedencia (`source_used`)**: Cuando a la fuente primaria le falta una marquesina arcade, carátula HD o tráiler/gameplay, `CandidateAggregator` completa automáticamente los activos faltantes desde las fuentes secundarias sin sobrescribir los metadatos primarios, informando la procedencia simple o compuesta (ej. `"source_used": "igdb+arcadeitalia"`). Si la fuente primaria no devuelve resultados o está en enfriamiento de cuota, pasa de inmediato a la fuente secundaria.
- **Referencial Relacional de 66 Sistemas (`GET /api/v1/games/filters`)**: Expone **66 sistemas retro, arcade, portátiles y microordenadores canónicos** (`cps1`, `cps2`, `cps3`, `neogeo`, `mame`, `megadrive`, `snes`, `pcengine`, `saturn`, `dreamcast`, `psx`, ...), cada uno con `supported_genres` y `supported_decades` (más alias `genres`/`decades`) para el filtrado dinámico por etiquetas en `ArcadeMatrix` (ESP32) y `ArcadeMatrix_RPi`.

```bash
curl -s "http://dmdmediashowcase.local:8890/api/v1/games/search?q=sf2x&lang=es&systems=cps2&genres=fighting&limit=8" | jq .
```

| Parámetro | Tipo | Descripción |
| :--- | :--- | :--- |
| `q` | `string` | Texto del título o alias de ROM arcade (`sf2x`, `streets of rage`, `akira`, `blade runner`). |
| `lang` | `string` | Idioma objetivo para título, sinopsis y carátulas regionales (`fr`, `en`, `es`, `de`, `it`, `ja`). |
| `systems` | `string` | IDs canónicos de sistemas entre las 66 plataformas (`cps2`, `neogeo`, `megadrive`, `snes`, `mame`, ...). |
| `genres` | `string` | IDs canónicos de géneros separados por comas (`fighting`, `shmup`, `platform`, `scifi`, ...). |
| `decades` | `string` | Filtro de décadas separado por comas (`1970s`, `1980s`, `1990s`, `2000s`, ...). |
| `limit` | `int` | Número máximo de candidatos devueltos (`1..25`, por defecto `8`). |

---

### 5.2 Clientes DMD Conectados y Perfiles Persistentes (`./data/dmd_profiles.json`)

- **`GET /api/v1/clients`** — Lista todos los paneles DMD descubiertos y configurados (`client` y `server`), su estado en línea, `config_source`, `supports_local_card`, geometría, `udp_ip:udp_port`, título activo y cola bajo demanda.
- **`POST /api/v1/clients/hello`** — Endpoint HTTP de anuncio / heartbeat (`ClientHelloPayload`) para receptores pasivos Modelo 2B sin MQTT.
- **`GET /api/v1/clients/{dmd_id}/profile`** y **`DELETE /api/v1/clients/{dmd_id}/profile`** — Consulta o elimina un perfil DMD guardado.
- **`PUT /api/v1/clients/{dmd_id}/profile`** — Guarda el perfil gestionado en el servidor y su `playlist` de rotación dentro de `./data/dmd_profiles.json`, recargando al instante su preparación `N+1`:

```bash
curl -X PUT "http://dmdmediashowcase.local:8890/api/v1/clients/esp32_passive_128x32/profile" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Topper Bartop 128x32",
    "config_source": "server",
    "supports_local_card": false,
    "always_stream_udp": true,
    "udp_ip": "192.168.1.60",
    "udp_port": 4210,
    "width": 128,
    "height": 32,
    "lang": "es",
    "inter_item_gap_s": 1.0,
    "playlist": [
      {
        "enabled": true,
        "domain": "games",
        "count": 2,
        "systems": ["neogeo", "cps2", "megadrive", "snes"],
        "genres": ["fighting", "shmup", "beatemup"],
        "decades": ["1990s"],
        "video_mode": "auto_action",
        "color_boost": "pixel_art",
        "jacket_panorama": true,
        "include_video": true,
        "video_max_duration_s": 10.0,
        "video_fps": 20,
        "text_scroll_speed": "6"
      },
      {
        "enabled": true,
        "domain": "anime",
        "count": 1,
        "video_type": "opening",
        "video_mode": "auto_action",
        "color_boost": "anime"
      }
    ]
  }'
```

---

### 5.3 Cola Prioritaria Bajo Demanda (`GET / POST / DELETE /api/v1/clients/{dmd_id}/queue`, `POST /queue/async`, `POST /api/v1/{domain}/on-demand`)

Consulta (`GET` — incluyendo la procedencia `source_used` de cada elemento), envía (`POST /queue` síncrono o `POST /queue/async` no bloqueante `< 3 ms` `202 Accepted`) o elimina (`DELETE /api/v1/clients/{dmd_id}/queue/{queue_id}`) un título específico en la cola prioritaria de un DMD — ya sea mediante el `candidate_id` devuelto por `/search` o mediante `{domain, query, systems, genres, decades, lang}` (también soportado en `POST /api/v1/{domain}/on-demand`):

- **`mode: "enqueue_next"`** *(Por defecto)*: Transcodifica el elemento con prioridad alta (`HIGH_ON_DEMAND`) y lo reproduce justo al terminar el elemento actual.
- **`mode: "play_now"`**: Interrumpe de inmediato la emisión en curso (`stop`/`play_card` por MQTT + corte del stream UDP `ffmpeg -re`) y lanza el título solicitado en cuanto termina su transcodificación.
- **`POST /api/v1/clients/{dmd_id}/queue/async`** *(Recomendado para firmware embebido y `FrontendSync`)*: Registra de inmediato un marcador `preparing` y planifica el scraping + transcodificación en segundo plano devolviendo `202 Accepted` en `< 3 ms` sin bloquear sockets HTTP.
- **`mark_seen: true | false`**: Controla si la reproducción se registra en `./data/seen_history.json`.

```bash
# Encolar por candidate_id obtenido en /api/v1/{domain}/search
curl -X POST "http://dmdmediashowcase.local:8890/api/v1/clients/esp32_passive_128x32/queue" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "movies",
    "candidate_id": "tmdb:movie:78",
    "lang": "es",
    "mode": "enqueue_next",
    "mark_seen": true
  }'
```

---

### 5.4 Control de Reproducción en Vivo (`POST /api/v1/clients/{dmd_id}/control`)

Controla cualquier DMD activo en tiempo real desde la WebUI de Showcase, Home Assistant o scripts externos (enrutado hacia MQTT `dmd_showcase/clients/{dmd_id}/command` y directamente sobre `FfmpegUdpClockStreamer` para receptores pasivos):

```bash
# Acciones soportadas: "skip" | "pause" | "resume" | "stop"
curl -X POST "http://dmdmediashowcase.local:8890/api/v1/clients/esp32_passive_128x32/control" \
  -H "Content-Type: application/json" \
  -d '{"action": "skip"}'
```

---

### 5.5 Integración `FrontendSync` (Batocera / RetroBat / LaunchBox / Playnite)

En máquinas recreativas o HTPC con **Batocera**, **RetroBat**, **LaunchBox / BigBox** o **Playnite**, **`FrontendSync`** convierte su DMD HUB75 en una marquesina/topper reactivo sincronizado con el juego explorado o iniciado por el jugador, regresando automáticamente a la rotación Showcase sin repetición cuando la máquina queda en reposo:

1. **Al Seleccionar o Iniciar un Juego (`POST /queue/async`, `mode: "play_now"`, `mark_seen: false`)**:
   - `FrontendSync` (tanto en `ArcadeMatrix` ESP32 como en `ArcadeMatrix_RPi`) invoca `POST /api/v1/clients/{dmd_id}/queue/async` con `mode: "play_now"` y **`mark_seen: false`**, recibiendo `202 Accepted` en `< 3 ms` mientras `DomainSourceChainService` resuelve alias de ROM arcade (ej. `sf2x` en `cps2`) y transcodifica en segundo plano.
   - **Por qué `mark_seen: false` es clave**: Jugar 20 veces a su clásico favorito de Neo-Geo, CPS-2 o Mega Drive **jamás contamina `./data/seen_history.json`**, preservando intacto el descubrimiento Consume-Once durante el modo attract.
2. **Al Salir del Juego (`action: "skip"`)**:
   - Al volver al menú o al modo reposo, `FrontendSync` envía `{"action": "skip"}` a `/api/v1/clients/{dmd_id}/control` para retomar al instante el siguiente elemento `N+1` precalentado.

```bash
# Ejemplo: Script EmulationStation en Batocera (/userdata/system/configs/emulationstation/scripts/game-start/dmd_sync.sh)
#!/bin/sh
SYSTEM="$1"    # ej. "cps2", "megadrive", "neogeo", "mame"
ROM_PATH="$2"
GAME_NAME="$3" # ej. "sf2x" o "Streets of Rage 2"

curl -fsS -m 2 -X POST "http://dmdmediashowcase.local:8890/api/v1/clients/esp32_passive_128x32/queue/async" \
  -H "Content-Type: application/json" \
  -d "{
    \"domain\": \"games\",
    \"query\": \"${GAME_NAME}\",
    \"systems\": [\"${SYSTEM}\"],
    \"lang\": \"es\",
    \"mode\": \"play_now\",
    \"mark_seen\": false
  }" >/dev/null 2>&1 &
```

---

### 5.6 Estudio Card Modo 2B, Simulador LED HUB75 y Promoción 0 ms en Cola

La pestaña **`4. Estudio Card y Sim DMD (2B)`** de la WebUI (`http://dmdmediashowcase.local:8890/`) ofrece un estudio interactivo de diseño y simulación para las Cards del Modo 2B y secuencias completas en formatos horizontales (**YOKO**: `64×32`, `128×32`, `64×64`, `128×64`, `256×64`) y verticales (**TATE**: `32×64`, `32×128`, `64×128`, `64×256`):

- **5 Diseños Pixel-Art (`card_layout`)**: `classic_split`, `poster_right`, `badge_header`, `hero_banner`, `minimal_arcade` (con adaptación automática en TATE).
- **5 Temas Visuales (`card_theme`)**: `tinted`, `pure_black`, `neon_border`, `poster_wash`, `amber_dmd` (monocromo plasma pinball `#FF7300`), junto con `card_font` (`arcade_pixel` 1-bit nítido con soporte CJK automático, o `clean_sans`), `text_scroll_speed` (`1..10`) y `card_hold_s`.
- **Endpoints Binarios `DMDS` de Doble Velocidad**:
  - `POST /api/v1/studio/card-preview`: Vista previa rápida de Card (`< 100 ms`) fuera del semáforo pesado de vídeo.
  - `POST /api/v1/studio/full-preview`: Secuencia completa `Card + Panoramas + 0..5 Capturas + Vídeo` con cabeceras `X-DMD-Preview-Id` y `X-DMD-Chapters`.
- **Perfiles de Estudio por Dominio (`domain_styles`) y Etiquetas Visuales de Rotación**: Cada perfil DMD en `./data/dmd_profiles.json` conserva 4 perfiles de estudio independientes (`domain_styles.games`, `domain_styles.movies`, `domain_styles.series`, `domain_styles.anime`) con su diseño de Card, tema, panoramas, capturas, encuadre (`video_mode`), `color_boost`, `video_max_duration_s` y `video_type`. La tabla de rotación pasiva (Pestaña 1) utiliza selectores visuales multietiqueta (`systems`, `genres`, `decades` desde `/api/v1/{domain}/filters`) con acceso directo **`🎨 Estudio [DOMINIO] →`** a la Pestaña 4.
- **Promoción en 0 ms a la Cola del DMD Activo**: Pasar `preview_id` (`sim_...`) a `POST /api/v1/clients/{dmd_id}/queue` inyecta la simulación ya renderizada en la cola del DMD activo en **`0 ms` sin recodificación** cuando coinciden las dimensiones, o guarda `domain_styles` permanentemente mediante `PUT /api/v1/clients/{dmd_id}/profile`.
