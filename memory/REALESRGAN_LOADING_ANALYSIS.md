# Real-ESRGAN Model Loading Analysis - HiImage Project

## Summary
Real-ESRGAN models are loaded at runtime in the `backend/core/upscaler.py` file. The models use a custom path resolution system based on the project root directory.

---

## 1. PRIMARY MODEL LOADING FILE

**File:** `/Users/hikari/Documents/Git/HiImage/backend/core/upscaler.py`

This is the **main file** that handles Real-ESRGAN model loading and inference.

### Key Components:

#### A. Path Resolution (Lines 85-90)
```python
# Line 85-86: Path construction
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_DIR = os.path.join(_PROJECT_ROOT, 'models', 'realesrgan')

# Line 89-90: Helper function to get weight path
def _get_weight_path(model_name: str) -> str:
    return os.path.join(_WEIGHTS_DIR, _MODEL_WEIGHT[model_name])
```

**Path Resolution Details:**
- `__file__` = `/Users/hikari/Documents/Git/HiImage/backend/core/upscaler.py`
- First dirname = `/Users/hikari/Documents/Git/HiImage/backend/core`
- Second dirname = `/Users/hikari/Documents/Git/HiImage/backend`
- `_PROJECT_ROOT` = `/Users/hikari/Documents/Git/HiImage/backend` (one level up from backend/core)
- Final `_WEIGHTS_DIR` = `/Users/hikari/Documents/Git/HiImage/backend/models/realesrgan`

**Note:** This is NOT using `PROJECT_ROOT` from `config.py`. It uses its own calculation.

#### B. Model Weight Download (Lines 93-124)
```python
def _download_weight(model_name: str, progress_callback=None) -> str:
    """下载模型权重，支持进度回调 callback(downloaded_bytes, total_bytes)"""
    os.makedirs(_WEIGHTS_DIR, exist_ok=True)  # Line 95
    url = _MODEL_URL[model_name]
    dest = _get_weight_path(model_name)
    # ... download logic ...
    urllib.request.urlretrieve(url, dest, reporthook=_reporthook)  # Line 115
    return dest
```

#### C. Lazy Loading (Lines 174-185)
**Line 174-185: `_ensure_model_loaded()` method**
```python
def _ensure_model_loaded(self):
    """懒加载：首次调用时初始化模型（含自动下载权重）"""
    if self._upsampler is not None:
        return
    
    # Line 180: Get weight path
    weight_path = _get_weight_path(self.model_name)
    
    # Line 181-183: Check if weight exists, if not download
    if not os.path.exists(weight_path):
        print(f"[Upscaler] 权重文件不存在，开始下载...")
        _download_weight(self.model_name)
    
    # Line 185: Build the actual upsampler
    self._upsampler = self._build_upsampler(weight_path)
```

#### D. Model Loading in PyTorch (Lines 187-252)
**Line 187-252: `_build_upsampler()` method - WHERE MODELS ARE ACTUALLY LOADED**

```python
def _build_upsampler(self, weight_path: str):
    # Line 197-204: Import Real-ESRGAN library
    from realesrgan import RealESRGANer
    from basicsr.archs.rrdbnet_arch import RRDBNet
    
    # Line 206-208: Get architecture config
    arch      = _MODEL_ARCH.get(self.model_name, "RRDBNet")
    num_block = _MODEL_NUM_BLOCK.get(self.model_name, 23)
    num_conv  = _MODEL_NUM_CONV.get(self.model_name, 32)
    
    # Line 210-229: Create appropriate architecture
    if arch == "SRVGGNetCompact":
        from basicsr.archs.srvgg_arch import SRVGGNetCompact
        model = SRVGGNetCompact(...)
    else:
        model = RRDBNet(...)
    
    # Line 241-250: CREATE RealESRGANer WITH model_path (ACTUAL LOADING)
    upsampler = RealESRGANer(
        scale=self.scale,
        model_path=weight_path,  # ← LINE 243: MODEL WEIGHTS PATH
        model=model,
        tile=0,
        tile_pad=10,
        pre_pad=0,
        half=half,
        gpu_id=gpu_id,
    )
    
    # Line 251: Log message
    print(f"[Upscaler] 模型加载完成: {self.model_name} (device={self.device}, scale={self.scale}x)")
    return upsampler
```

**CRITICAL LINE:** **Line 243: `model_path=weight_path`** - This is where the weight file is passed to RealESRGANer for actual loading.

#### E. Upscaling Execution (Lines 148-168)
**Line 158: Trigger model loading**
```python
def upscale(self, image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("输入图像为空")
    
    self._ensure_model_loaded()  # Line 158: TRIGGERS LAZY LOADING
    
    img_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    
    # Line 164: USE the loaded model for inference
    output_bgr, _ = self._upsampler.enhance(img_bgr, outscale=self.scale)
    
    return cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)
```

---

## 2. CONFIG FILE

**File:** `/Users/hikari/Documents/Git/HiImage/backend/app/config.py`

### MODELS_DIR Definition (Lines 9-11)
```python
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "settings.json")
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")  # Line 11
```

**IMPORTANT:** `config.py` defines `MODELS_DIR`, but **upscaler.py does NOT use it**. 
- `config.py`: `MODELS_DIR = /Users/hikari/Documents/Git/HiImage/models`
- `upscaler.py`: `_WEIGHTS_DIR = /Users/hikari/Documents/Git/HiImage/backend/models/realesrgan`

---

## 3. ENTRY POINTS (How models are loaded at runtime)

### Entry Point 1: API Router
**File:** `/Users/hikari/Documents/Git/HiImage/backend/app/routers/upscale.py`

**Line 66-80: POST `/upscale` endpoint**
```python
@router.post("/upscale")
async def upscale_image(req: UpscaleRequest):
    from core.upscaler import Upscaler  # Line 68: Import
    
    def _process():
        upscaler = Upscaler(model_name=req.model, device=req.device)  # Line 80: CREATE INSTANCE
        return upscaler.upscale(image)  # Line 81: CALL upscale() → triggers _ensure_model_loaded()
    
    result = await loop.run_in_executor(executor, _process)
```

**Flow:** API call → `Upscaler()` instance created → `.upscale()` called → `_ensure_model_loaded()` → `_build_upsampler(weight_path)` → `RealESRGANer(model_path=weight_path)`

### Entry Point 2: Pipeline
**File:** `/Users/hikari/Documents/Git/HiImage/backend/core/pipeline.py`

**Line 144-146:**
```python
if cfg.upscale.enabled:
    from core.upscaler import Upscaler  # Line 144: Import
    upscaler = Upscaler(model_name=cfg.upscale.model, device=cfg.upscale.device)  # Line 145
    result = upscaler.upscale(result)  # Line 146: Same flow as above
```

---

## 4. MODEL WEIGHT FILE LOCATION AT RUNTIME

### Dynamic Path Resolution
```
Weight file path = _WEIGHTS_DIR + _MODEL_WEIGHT[model_name]
                 = /Users/hikari/Documents/Git/HiImage/backend/models/realesrgan + {weight_filename}
```

### Example for `RealESRGAN_x4plus`:
- Weight filename from `models.yaml`: `RealESRGAN_x4plus.pth` (or similar)
- Full path: `/Users/hikari/Documents/Git/HiImage/backend/models/realesrgan/RealESRGAN_x4plus.pth`

---

## 5. KEY FINDINGS

| Item | Location | Details |
|------|----------|---------|
| **Main Model Loading File** | `/Users/hikari/Documents/Git/HiImage/backend/core/upscaler.py` | Lines 187-252 |
| **Actual Model Loading Line** | Line 243 | `model_path=weight_path` passed to `RealESRGANer()` |
| **Weight Path Construction** | Lines 85-86 | `_WEIGHTS_DIR = /Users/hikari/Documents/Git/HiImage/backend/models/realesrgan` |
| **Lazy Loading Trigger** | Line 158 | `self._ensure_model_loaded()` in `upscale()` method |
| **Download Check** | Lines 180-183 | Check if weight exists, download if needed |
| **Config MODELS_DIR** | `/Users/hikari/Documents/Git/HiImage/backend/app/config.py`, Line 11 | Defined but NOT used by upscaler |
| **API Entry Point** | `/Users/hikari/Documents/Git/HiImage/backend/app/routers/upscale.py`, Line 66 | POST `/upscale` endpoint |
| **Pipeline Entry Point** | `/Users/hikari/Documents/Git/HiImage/backend/core/pipeline.py`, Line 144 | Pipeline class integration |

---

## 6. EXECUTION FLOW DIAGRAM

```
User/API Call
    ↓
POST /upscale (router)
    ↓
Upscaler.__init__() [Line 136]
    ↓
Upscaler.upscale(image) [Line 148]
    ↓
_ensure_model_loaded() [Line 174]
    ├─ Check weight_path exists [Line 181]
    ├─ If not: _download_weight() [Line 183]
    └─ _build_upsampler(weight_path) [Line 185]
         ├─ Create architecture (RRDBNet/SRVGGNetCompact) [Lines 210-229]
         ├─ RealESRGANer(model_path=weight_path, ...) [Line 241-250] ← ACTUAL LOADING
         └─ Return upsampler instance
    ↓
upsampler.enhance(img_bgr, outscale=scale) [Line 164]
    ↓
Return upscaled image
```

