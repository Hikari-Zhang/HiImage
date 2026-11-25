# QUALITY-01 去水印画质基准

版本 2 保留 30 个合成样本，新增 18 个以真实照片为底图的受控样本。每种背景配半透明文字、重复文字、倾斜文字、图标、不透明文字和无水印对照。实拍样本使用人为叠加的水印来保留干净原图和像素级真值。报告记录 Pillow、NumPy 版本和原图、输入、遮罩的像素哈希。

## 实拍底图及许可

底图随仓库保存，评测时可离线读取。原始 PNG 与 scikit-image v0.25.2 上游登记的 SHA-256 一致。生成样本时等比例缩放并居中裁剪至 512 × 384。复跑可使用报告中的 Pillow 和 NumPy 版本，并核对像素哈希。相同场景的六种样本共用一张底图，因此统计是小样本基线。

| 底图 | 作者与许可 | 来源 |
| --- | --- | --- |
| Chelsea 猫 | Stefan van der Walt，CC0 1.0 | [来源与许可说明](https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.chelsea) · [固定版本原图](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/chelsea.png) |
| 移动拍摄的挂钟 | Stefan van der Walt，摄影师发布为公有领域 | [来源与许可说明](https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.clock) · [固定版本原图](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/clock_motion.png) |
| 咖啡杯 | Rachel Michetti，CC0 1.0，Pikolo Espresso Bar 提供 | [来源与许可说明](https://scikit-image.org/docs/0.25.x/api/skimage.data.html#skimage.data.coffee) · [固定版本原图](https://raw.githubusercontent.com/scikit-image/scikit-image/e8a42ba85aaf5fd9322ef9ca51bc21063b22fcae/skimage/data/coffee.png) |

## 运行方式

启动后端并准备 LaMa 和 FLUX Fill 权重。完整对比需要 CUDA。使用项目的 Python 环境，在仓库根目录运行下面的命令。每次运行指定一个新的输出目录。

```sh
python -m scripts.benchmark_watermark_quality --output tmp/hiimage-quality-synthetic
python -m scripts.benchmark_watermark_quality --corpus photo --models wm_lama --detect --output tmp/hiimage-quality-photo-cpu
python -m scripts.benchmark_watermark_quality --corpus photo --sample photo-coffee-translucent --sample photo-clock-tilted --steps 12 --output tmp/hiimage-quality-photo-ab
```

默认合成集使用同一份图片和遮罩调用两个模型，遮罩扩张为 0。FLUX Fill 固定提示词、引导强度 15、随机种子 42，默认 30 步。用 `--corpus all` 合并两个样本集，用 `--sample` 重复指定样本，用 `--steps 2` 做快速冒烟。后端地址可通过 `--base-url` 指定。

每个样本目录保存原图、带水印图、遮罩及模型输出。修复报告记录模型参数、请求耗时、遮罩内平均绝对误差和遮罩外改动像素数。误差以无水印原图为参照，另需并排查看纹理、文字及主体细节。空遮罩样本跳过修复。接口失败、输出尺寸变化或遮罩外像素变化会使脚本以非零状态退出，已得到的结果仍会写入报告。

`--detect` 同时调用自动检测接口，`--detect-only` 仅测检测，默认灵敏度 0.5，可用 `--sensitivity` 修改。评测取接口返回的全部候选框，以 IoU ≥ 0.30 与水印真值框做一对一匹配。重复水印的每处叠加均单独计数。未匹配的候选计误检，未匹配的水印框计漏检。报告记录总精确率、召回率，以及无水印对照中出现候选的图片数。检测结果独立计分，修复仍使用真值遮罩。

## 2026-11-25 Windows 实测

LaMa 在 Intel Core Ultra 9 285K 上用 CPU 运行，FLUX Fill 在 RTX 5080 上以 CUDA 跑 12 步。请求耗时包括编码、模型冷启动、推理和返回结果。两张实拍样本的四次修复均保持原尺寸，遮罩外改动均为 0 像素。运行期间以 1 秒间隔采样整卡显存，起始占用 12,932 MiB，峰值 13,535 MiB，最低空闲 2,445 MiB。该采样包含同机其他进程。

| 实拍样本 | 输入遮罩内 MAE | LaMa MAE / 耗时 | FLUX Fill MAE / 耗时 |
| --- | ---: | ---: | ---: |
| 挂钟，倾斜文字 | 19.79 | 4.20 / 29.38 秒 | 8.98 / 126.80 秒 |
| 咖啡杯，半透明文字 | 51.80 | 18.06 / 28.40 秒 | 61.48 / 103.97 秒 |

挂钟的两个结果仍能看到细字形轮廓。咖啡杯的水印横跨亮色杯口与深色杯身，LaMa 输出仍有浅色字边，FLUX Fill 输出在相同位置出现更明显的纹理错位，遮罩内误差也高于修复前。该次 A/B 覆盖两张实拍照片、零扩张真值遮罩和固定提示词，画质差异仍需在更多底图和遮罩条件下验证。

### 实拍全量 LaMa 与检测

- LaMa 完成 15 张有水印样本，另外 3 张干净对照跳过修复。15 张的遮罩内误差均有下降，按样本等权计算的平均 MAE 从 56.63 降至 16.74。遮罩外合计 0 个像素改动，请求耗时中位数 12.00 秒。
- 18 张均使用 PaddleOCR ONNX 完成自动检测，灵敏度 0.5。21 个水印目标中匹配 15 个、漏检 6 个，另有 10 个误检候选，精确率 60.0%，召回率 71.4%。3 张无水印对照均有候选，共 4 个。3 个图标水印均未匹配，请求耗时中位数 1.31 秒。
- 检测评分覆盖全部返回候选，按接口现状考察人工确认前的结果。小样本上的主要短板是图标漏检和无水印图片误报。此次试验未覆盖自然形成的水印及人脸照片。

## 去水印模型参数扫描

扫描使用实拍底图与已知水印遮罩。综合误差为水印像素 MAE 加上遮罩外 10 像素范围内的背景 MAE 的一半。所有档位按同一组像素计算，分数越低越好。另检查扩张后遮罩以外的像素是否保持不变。

LaMa 在 15 个带水印样本上分别测了 0、2、4、6、10 像素扩张，共 75 次。平均综合误差依次为 16.74、7.88、9.89、12.41、17.68。默认值选用 2 像素。真实 API 省略扩张参数时，咖啡杯样本的输出与 2 像素试验逐像素一致。

FLUX Fill 先在咖啡杯样本上对比扩张、步数和引导强度，再用挂钟和猫脸复验筛选出的两档。筛选中，旧默认 10 像素、50 步、引导 30 的综合误差是 103.17。入围的 0 像素、12 步、引导 15 和 2 像素、12 步、引导 30 分别为 61.48 和 67.44，两张复验图的平均误差分别为 25.35 和 18.10。2 像素档改用空提示词后，咖啡杯误差升至 72.14，因此保留原提示词和种子 42。默认值选用 2 像素、12 步、引导 30。咖啡杯仍有可见重绘字形，本轮 3 张底图上的排序适用于该组样本及遮罩。

去水印默认值集中在 `backend/core/models.yaml` 的模型条目 `watermark_defaults` 中。页面和修复接口都读取该配置。任务显式指定的参数优先，设置页备用扩张值用于没有专属配置的模型。

FLUX Fill 的真实 CUDA API 调用省略遮罩扩张和重绘参数后，挂钟输出与入选试验逐像素一致。两种模型的实测输出均保持扩张后遮罩外像素不变。

### FLUX Fill 视觉复查

咖啡杯入选参数的综合误差为 67.44，输出仍生成了新字形。进一步保持 12 步和 2 像素扩张，降低引导到 6、改用整行遮罩，以及在整行遮罩下使用场景提示词，分别得到 71.98、85.85 和 68.36。放大检查发现三档仍有新字形或条带，整行遮罩在杯口附近影响更大。LaMa 2 像素在同一水印遮罩上的综合误差为 10.41，杯口轮廓更完整。本轮 FLUX Fill 各档未通过该样本的视觉验收，去水印页默认使用 LaMa，并在重绘参数旁提示文字伪影。
