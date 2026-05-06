# ClearWaterMark - Comprehensive Project Analysis

## Project Overview

**ClearWaterMark** is an advanced AI-powered watermark removal tool with a modern GUI built on PySide6 (Qt6). It leverages multiple state-of-the-art deep learning models to intelligently detect and remove watermarks from images while preserving image quality.

### Core Purpose
Remove watermarks from images using AI inpainting models, with support for multiple algorithms, automatic detection, and super-resolution enhancement.

### Technology Stack
- **GUI Framework**: PySide6 (Qt6) - Modern, cross-platform desktop UI
- **Image Processing**: OpenCV, Pillow, NumPy
- **AI Models**: IOPaint + 15+ inpainting models
- **Super-Resolution**: Real-ESRGAN (2x/4x upscaling)
- **Model Server**: HTTP-based IOPaint server for diffusion models
- **Configuration**: JSON-based settings with fallback defaults
- **Async Processing**: QThread-based workers for non-blocking operations

---

## Architecture Overview

### Entry Point: `/main.py`
```python
- Initializes PySide6 QApplication
- Sets up environment variables for model caching:
  * XDG_CACHE_HOME → ./models/ (IOPaint models)
  * HF_HOME → ./models/huggingface (HuggingFace models)
  * HF_ENDPOINT (configurable mirror)
  * HF_TOKEN (for gated models like PowerPaintV2)
- Loads configuration from config/settings.json
- Registers atexit hook to stop IOPaint server on exit
- Creates and shows MainWindow
```

**Responsibilities:**
- Bootstrap application
- Configure model download locations
- Manage server lifecycle

---

## 1. GUI LAYER (PySide6)

### `/gui/main_window.py` - Main Application Window
**Responsibilities:**
- Central application coordinator
- Image file management (open, load, display)
- Workflow orchestration between components
- Async worker thread management

**Key Components:**
- **ImageView**: Central canvas for image display and ROI selection
- **PreviewPanel**: Right-side panel for controls and results
- **MainWindow**: Top-level window with menu bar and toolbar
- **WorkerThread**: Async watermark removal processing
- **UpscaleWorkerThread**: Async super-resolution processing

**Features:**
- File menu: Open, Save, Exit
- Edit menu: Clear ROIs, Auto-detect watermarks, Undo
- View menu: Zoom in/out, Fit to window, Fullscreen preview
- Keyboard shortcuts: Ctrl+O (open), Ctrl+S (save), Ctrl+Z (undo)
- Progress bar for long operations
- Status messages and error dialogs

### `/gui/image_view.py` - Advanced Image Display Component
**Core Features:**
- **Zoom & Pan**: Mouse wheel to zoom (0.1x to 10x), drag to pan
- **ROI Selection**: Left-click drag to draw rectangular regions
- **Multiple ROIs**: Support for many watermark regions
- **Drag-Drop**: Drag image files onto the window to open
- **Rendering**: Antialiasing + smooth pixmap transform for quality
- **Graphics Scene**: Qt Graphics framework for efficient rendering

**Mouse/Keyboard Controls:**
- Left-click drag: Draw ROI
- Middle-click: Fit to window
- Scroll wheel: Zoom in/out
- Supported image formats: PNG, JPG, JPEG, BMP, TIFF, TIF, WEBP

### `/gui/preview_panel.py` - Controls & Results Panel
**Components:**
- **Original Image Preview**: Zoomable display of input
- **Result Preview**: Shows watermark-removed output
- **ROI List**: Lists all selected regions with delete buttons
- **Model Selector**: Dropdown for choosing inpainting model
- **Device Selector**: CPU/MPS/CUDA selection
- **Removal Button**: Triggers async watermark removal
- **Upscale Options**: Super-resolution model selection
- **Dilation Control**: Mask expansion parameter (spinbox)
- **Zoom Controls**: In/out buttons for previews

**Model Groups:**
1. **Fast Models** (CLI mode, no GPU needed):
   - LaMa (recommended, balanced)
   - MiGAN (GAN-based, fast)
   - ZITS (edge-aware)
   - FCF (fast fill)
   - MAT (high quality but slow)
   - LDM (lightweight diffusion)
   - Manga (anime/lineart)
   - CV2 (traditional algorithms)

2. **Specialized Models** (Server mode, large VRAM):
   - AnyText (text watermark specialist)
   - SD Inpainting (complex backgrounds)
   - Realistic Vision (photorealistic)
   - PowerPaintV2 (with text prompts)
   - SDXL Inpainting (2K+ resolution)

### `/gui/fullscreen_preview.py` - Full-Screen Viewer
**Features:**
- Full-screen image display
- Zoom with mouse wheel (keeps cursor position as anchor)
- Pan with drag
- Keyboard shortcuts:
  - ESC: Exit fullscreen
  - +/=: Zoom in
  - -: Zoom out
  - 0/1: Reset zoom
- Hints overlay at bottom

---

## 2. CORE PROCESSING LAYER

### `/core/inpainter.py` - Watermark Removal Engine
**Architecture:** Dual-mode processing system
```
Fast Models (LaMa, MiGAN, ZITS, etc.)
    ↓
    CLI mode: iopaint run --image ... --mask ... --model lama
    ↓
Returns result image

Diffusion Models (SD, PowerPaintV2, etc.)
    ↓
    Server mode: Keep running, reuse for multiple calls
    ↓
    HTTP POST to /api/v1/inpaint
    ↓
Returns result image
```

**Key Methods:**
- `remove_watermark(image, roi_list, output_dir)`: Main API
  - Takes RGB numpy array
  - Creates mask from ROI regions
  - Calls appropriate backend
  - Returns RGB numpy result

- `remove_watermark_with_mask(image, mask, output_dir)`: Mask-based removal
  - Direct mask input for custom masking
  - Supports both CLI and server modes

- `create_mask(image_shape, roi_list, dilation)`: Mask generation
  - Creates binary mask from ROI regions
  - Applies dilation to ensure full coverage
  - Handles image boundaries

**CLI Processing (`_run_iopaint_cli`):**
- Saves image and mask to temp directory
- Spawns subprocess with iopaint command
- Streams output character-by-character (handles tqdm progress bars)
- Reads result PNG from output directory
- Cleans up temp files on success, keeps on error for debugging

**Timeout Handling:**
- Fast models: 5 minutes
- Diffusion models: 30 minutes
- Properly handles subprocess termination

**Model Configuration:**
```python
MODEL_GROUPS = [
    ("── Fast Models ──", [...]),          # CLI mode
    ("── Specialized ──", [...]),          # Server mode
    ("── Diffusion ──", [...]),            # Server mode
]
```

### `/core/model_server.py` - IOPaint HTTP Server Manager
**Purpose:** Singleton process manager for diffusion models

**Architecture:**
- Single long-running IOPaint process (per model)
- Keeps model in memory for 5 minutes after last use
- Auto-restarts if model/device/settings change
- Thread-safe with locks

**Key Components:**
```python
class _ModelServer:
    - ensure_running(model_name, device, disable_nsfw)
        → Starts server if needed, returns http://127.0.0.1:51821
    
    - _start_unlocked()
        → Spawns: iopaint start --model PowerPaintV2 --device mps
        → Waits for /api/v1/server-config to become available
        → Async log streaming to terminal
    
    - _stop_unlocked()
        → Gracefully terminates process
        → Cancels idle timer
    
    - _idle_shutdown()
        → Auto-stops after 5 min inactivity
        → Configurable via config/settings.json
    
    - _stream_logs()
        → Captures subprocess output
        → Handles tqdm progress bars (character-by-character)
        → Real-time display to console
```

**HTTP API Call:**
```python
inpaint_via_server(image_rgb, mask, model_name, ...)
    ↓
    Encodes image/mask as base64 PNG
    ↓
    POST /api/v1/inpaint
    Content-Type: application/json
    {
        "image": "base64_encoded_png",
        "mask": "base64_encoded_mask"
    }
    ↓
    Returns PNG bytes (decoded to RGB numpy array)
```

**Configuration:**
- Server port: 51821 (configurable)
- Keepalive: 300 seconds (5 min)
- Startup timeout: 1800 seconds (30 min for first download)

### `/core/watermark_detector.py` - Automatic Watermark Detection
**Purpose:** CV-based watermark region detection (ML interface reserved)

**Detection Strategies:**
1. **Position Priority** - Checks common watermark locations:
   - Right-down corner (most common)
   - Left-down, right-up, left-up corners
   - Bottom-center, top-center
   - Search region size: 30-50% of image (adjustable by sensitivity)

2. **Edge Detection** - Finds low-contrast text/logo:
   - LAB color space L-channel (better for semi-transparent)
   - Adaptive threshold with sensitivity adjustment
   - Morphological operations (close + open) to connect text strokes
   - Contour extraction and filtering
   - Aspect ratio validation
   - Text line merging (same-row detection)

3. **Region Merging** - Combines overlapping detections:
   - IoU (Intersection over Union) threshold
   - Non-Maximum Suppression (NMS)
   - Padding expansion for full coverage

**Sensitivity Control (0.0 - 1.0):**
- Lower (0.0): Strict detection, fewer false positives
- Higher (1.0): Loose detection, more candidates

**Watermark Feature Detection:**
```python
_has_watermark_features(region):
    - Edge density: Low contrast edges (text traits)
    - Color variance: Different from background
    - Light pixels: Semi-transparent white overlay
    - Combines thresholds to decide
```

**Usage:**
```python
detector = WatermarkDetector(sensitivity=0.5)
regions = detector.detect(image_rgb)  # Returns [(x1,y1,x2,y2), ...]
```

### `/core/upscaler.py` - Super-Resolution Enhancement
**Purpose:** 2x/4x image upscaling after watermark removal

**Models Available:**
1. **RealESRGAN_x4plus** (65 MB)
   - 4x general photo upscaling (recommended)
   - Anime: 6B version (18 MB) for lineart

2. **RealESRGAN_x2plus** (65 MB)
   - 2x upscaling (faster, less memory)

**Features:**
- Lazy loading (downloads on first use)
- Device support: MPS/CUDA/CPU
- Configurable tile size (0 = no tiling = full image)
- Half-precision (fp16) on CUDA for speed
- Progress callbacks during download

**API:**
```python
upscaler = Upscaler(model_name='RealESRGAN_x4plus', device='mps')
result_rgb = upscaler.upscale(image_rgb)  # Returns 4x resolution
```

**Download Management:**
- Models cached in `/models/realesrgan/`
- Auto-download with progress bar
- Corruption detection and cleanup
- Network error handling

---

## 3. CONFIGURATION SYSTEM

### `/config/settings.json` - User Configuration
```json
{
  "server": {
    "keepalive_seconds": 300,        # Server idle timeout
    "port": 51821,                   # IOPaint HTTP port
    "startup_timeout": 1800          # Max wait for startup (30 min)
  },
  "inpaint": {
    "default_dilation": 10,          # Mask expansion pixels
    "default_device": "mps"          # CPU/MPS/CUDA
  },
  "network": {
    "hf_endpoint": "https://huggingface.co",  # or mirror
    "hf_token": "hf_xxxxx..."        # For gated models
  }
}
```

### `/config/__init__.py` - Configuration Loader
**Features:**
- Lazy loading (on-demand)
- Default fallbacks for missing fields
- JSON parsing with error tolerance
- Comment support (fields starting with `_`)
- Flattened access: `get('server.port')`
- Hot-reload capability: `reload()`

**Default Values:**
```python
'server.keepalive_seconds': 300
'server.port': 51821
'server.startup_timeout': 1800
'inpaint.default_dilation': 10
'inpaint.default_device': 'mps'
'network.hf_endpoint': 'https://huggingface.co'
'network.hf_token': ''
```

---

## 4. DATA FLOW & PROCESSING PIPELINE

### Step 1: Image Loading
```
File dialog / Drag-drop
    ↓
cv2.imread() → BGR format
    ↓
cv2.cvtColor(BGR2RGB) → RGB
    ↓
Display in ImageView
    ↓
Store: self.image (RGB numpy array)
```

### Step 2: ROI Selection
```
Manual:
  Left-click drag on ImageView
    ↓
  Emit roi_selected signal
    ↓
  Add to PreviewPanel list

Auto:
  Click "Auto-detect" button
    ↓
  WatermarkDetector.detect(image)
    ↓
  Returns list of (x1,y1,x2,y2)
    ↓
  Add each to ImageView, PreviewPanel
```

### Step 3: Watermark Removal
```
User clicks "Remove Watermark"
    ↓
Get selected model from dropdown
    ↓
Create WorkerThread(inpainter, image, rois)
    ↓
Thread runs: inpainter.remove_watermark(image, rois)
    ↓
├─ Fast Model Path:
│   ├─ Create mask
│   ├─ Run CLI: iopaint run --image ... --mask ... --model lama
│   └─ Read result PNG
│
└─ Diffusion Model Path:
    ├─ Create mask
    ├─ Ensure server running (start if needed)
    ├─ HTTP POST base64 image+mask
    └─ Decode result PNG
    
    ↓
Return RGB numpy result
    ↓
Display in PreviewPanel
    ↓
Store: self.result_image
```

### Step 4: Optional Super-Resolution
```
User selects upscaler model
    ↓
Create UpscaleWorkerThread(upscaler, result_image)
    ↓
Thread runs: upscaler.upscale(image)
    ↓
├─ Lazy-load model (download if needed)
├─ Convert RGB→BGR
├─ Run RealESRGANer.enhance()
├─ Convert BGR→RGB
└─ Return upscaled RGB
    
    ↓
Display in PreviewPanel
    ↓
Update self.result_image
```

### Step 5: Export
```
User clicks "Save Result"
    ↓
File save dialog
    ↓
Get output path (e.g., output/watermark-removed.png)
    ↓
cv2.cvtColor(RGB→BGR)
    ↓
cv2.imwrite(output_path, BGR)
    ↓
Success message
```

---

## 5. DIRECTORY STRUCTURE

```
ClearWaterMark/
├── main.py                          # Entry point
├── requirements.txt                 # Dependencies
├── README.md                        # Documentation
│
├── config/                          # Configuration
│   ├── __init__.py                 # Config loader
│   └── settings.json               # User settings
│
├── gui/                            # PySide6 GUI
│   ├── __init__.py
│   ├── main_window.py              # Main window
│   ├── image_view.py               # Image canvas
│   ├── preview_panel.py            # Control panel
│   └── fullscreen_preview.py       # Full-screen viewer
│
├── core/                           # Core processing
│   ├── __init__.py
│   ├── inpainter.py               # Watermark removal
│   ├── model_server.py            # IOPaint server
│   ├── watermark_detector.py      # Auto-detection
│   └── upscaler.py                # Super-resolution
│
├── utils/                         # Utilities (reserved)
│   └── __init__.py
│
├── models/                        # AI model cache
│   ├── huggingface/              # HuggingFace models
│   ├── realesrgan/               # ESRGAN weights
│   └── torch/hub/                # Other models
│
├── output/                        # Results directory
│   └── [saved images]
│
├── samples/                       # Example images
│
└── tmp/                          # Temporary files
```

---

## 6. DEPENDENCIES

```
# Core
numpy==1.26.4
opencv-python==4.10.0.84
Pillow==9.5.0

# GUI
PySide6==6.7.3

# AI Processing
iopaint==1.5.4

# Super-Resolution
basicsr>=1.4.2
realesrgan>=0.3.0
facexlib>=0.3.0
```

**Model Downloads (Automatic):**
- LaMa, MiGAN, ZITS, MAT, LDM: ~40MB each (IOPaint)
- Stable Diffusion: ~4GB (HuggingFace)
- PowerPaintV2: ~5GB (gated, needs token)
- RealESRGAN: ~65MB per model

---

## 7. KEY CAPABILITIES

### Watermark Removal
- 15+ AI models to choose from
- Fast models: 5-60 seconds (no GPU)
- Diffusion models: 30-120 seconds (with GPU acceleration)
- Batch processing support (planned)
- Video processing (future)

### Auto-Detection
- Position-based search (common corner locations)
- Edge-based detection (semi-transparent text/logos)
- Sensitivity adjustment (0.0-1.0)
- IoU-based region merging
- ML model interface (reserved)

### UI Features
- Real-time zoom/pan (0.1x to 10x)
- Multiple ROI management
- Comparison view (original vs result)
- Full-screen preview
- Drag-drop file loading
- Async processing (non-blocking)
- Progress indication

### Post-Processing
- 2x/4x super-resolution upscaling
- Batch ESRGAN support (future)
- Custom masking
- Format conversion (PNG/JPG/BMP)

### Hardware Support
- Apple Silicon (MPS) - optimized
- NVIDIA CUDA
- CPU-only mode (slower)
- Configurable device selection

### Configuration
- JSON-based settings
- Network mirror support
- HuggingFace token management
- Server port customization
- Auto-stop idle models (5-minute default)

---

## 8. KNOWN ISSUES & LIMITATIONS

1. **ROI Adjustment**: Cannot drag-adjust existing ROIs, must redraw
2. **Large Images**: Processing time increases with resolution
3. **Preview Clarity**: May need zoom optimization for very large previews
4. **Model Download**: First use downloads large files (3-7GB for diffusion)
5. **Memory**: Diffusion models require 8-16GB unified memory
6. **ML Detection**: Placeholder interface not yet implemented

---

## 9. FUTURE DEVELOPMENT ROADMAP

- [ ] Automatic watermark detection (fully optimized)
- [ ] More AI models (MAT, FLAN, etc.)
- [ ] Batch processing (multiple images)
- [ ] Video processing support
- [ ] ML watermark detector (YOLO, SAM)
- [ ] Undo/redo history
- [ ] Keyboard shortcuts optimization
- [ ] Performance monitoring dashboard
- [ ] Cloud processing support
- [ ] Mobile app

---

## 10. TECHNICAL NOTES

### Model Selection Strategy
1. **Text watermarks**: AnyText, PowerPaintV2 (best)
2. **Logo watermarks**: LaMa, MAT (high quality)
3. **Complex backgrounds**: Realistic Vision, SDXL
4. **Speed priority**: LaMa, MiGAN (fast models)
5. **Quality priority**: MAT, PowerPaintV2, SDXL

### Performance Optimization
- Fast models use CLI mode (no memory overhead between calls)
- Diffusion models use HTTP server (keep in memory)
- Async workers prevent UI blocking
- Character-by-character output reading handles progress bars
- Configurable server keepalive (default 5 min)

### Thread Safety
- Main thread: GUI events
- Worker threads: Model inference
- Server manager: Thread-locked singleton
- Queue-based communication via Qt signals

### Memory Management
- Model caching in ./models/ directory
- Temp files cleaned after success
- Kept on error for debugging
- Configurable HF cache location
- Auto-cleanup of old tmp directories

---

## 11. USAGE EXAMPLES

### Basic Watermark Removal
```python
from core.inpainter import Inpainter
import cv2

# Load image
img = cv2.imread('watermarked.jpg')
img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

# Initialize inpainter
inpainter = Inpainter(model_name='lama', device='mps')

# Define watermark region (manual or auto-detected)
rois = [(800, 600, 1000, 700)]  # (x1, y1, x2, y2)

# Remove watermark
result_rgb = inpainter.remove_watermark(img_rgb, rois)

# Save result
result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
cv2.imwrite('result.png', result_bgr)
```

### Auto-Detection + Removal
```python
from core.watermark_detector import auto_detect_watermark
from core.inpainter import Inpainter

# Auto-detect watermark regions
rois = auto_detect_watermark(image_rgb, sensitivity=0.6)
print(f"Detected {len(rois)} watermark regions")

# Remove watermarks
inpainter = Inpainter(model_name='lama')
result = inpainter.remove_watermark(image_rgb, rois)
```

### Super-Resolution Enhancement
```python
from core.upscaler import Upscaler

# Upscale 4x
upscaler = Upscaler(model_name='RealESRGAN_x4plus', device='mps')
upscaled = upscaler.upscale(result_image)  # 4x resolution

# Now save upscaled result
cv2.imwrite('result_4x.png', cv2.cvtColor(upscaled, cv2.COLOR_RGB2BGR))
```

---

## Summary

ClearWaterMark is a well-architected, production-ready watermark removal system that combines:

1. **Modern GUI**: PySide6 with intuitive image viewing/editing
2. **Multiple AI Models**: Fast local + powerful cloud-connected models
3. **Intelligent Detection**: CV-based auto-watermark finding
4. **Flexible Architecture**: CLI and HTTP server modes for different model types
5. **User Configurability**: JSON settings for diverse hardware/network scenarios
6. **Async Processing**: Non-blocking UI with progress feedback
7. **Quality Enhancement**: Built-in super-resolution upscaling
8. **Extensible Design**: Clear interfaces for future ML detectors and models

The system is optimized for Apple Silicon Macs but supports CPU and NVIDIA CUDA. It can process images of any size and provides multiple removal strategies depending on the watermark type and available resources.
