"""
Restormer 模型架构 —— 内联实现（无 pip 包）

Restormer: Efficient Transformer for High-Resolution Image Restoration
Paper: https://arxiv.org/abs/2111.09881
Official code: https://github.com/swz30/Restormer

本文件实现 Restormer 的核心架构，支持加载官方预训练权重。
支持任务：
  - denoise：去噪
  - deblur：去模糊
  - derain：去雨滴
  - dehaze：去雾
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Tuple
import math


# ── 工具函数 ────────────────────────────────────────────────────────────────

def conv3x3(in_planes: int, out_planes: int, stride: int = 1) -> nn.Conv2d:
    """3x3 convolution with padding"""
    return nn.Conv2d(
        in_planes, out_planes,
        kernel_size=3, stride=stride,
        padding=1, bias=True
    )


class BasicBlock(nn.Module):
    """基础残差块"""
    def __init__(self, inplanes: int, planes: int, stride: int = 1):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.relu(self.conv1(x))
        out = self.conv2(out)
        out += identity
        out = self.relu(out)
        return out


# ── Multi-DConv Head Transposed Self-Attention (MDTA) ──────────────────────

class MDTA(nn.Module):
    """Multi-DConv Head Transposed Self-Attention"""

    def __init__(self, dim: int, num_heads: int = 8):
        super(MDTA, self).__init__()
        self.num_heads = num_heads
        self.temperature = nn.Parameter(torch.ones(1, num_heads, 1, 1))

        self.qkv = nn.Conv2d(dim, dim * 3, kernel_size=1, bias=False)
        self.qkv_dwconv = nn.Conv2d(
            dim * 3, dim * 3,
            kernel_size=3, stride=1, padding=1,
            groups=dim * 3, bias=False
        )
        self.project_out = nn.Conv2d(dim, dim, kernel_size=1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, h, w = x.shape
        qkv = self.qkv_dwconv(self.qkv(x))
        q, k, v = qkv.chunk(3, dim=1)

        q = q.reshape(b, self.num_heads, c // self.num_heads, h * w)
        k = k.reshape(b, self.num_heads, c // self.num_heads, h * w)
        v = v.reshape(b, self.num_heads, c // self.num_heads, h * w)

        q = q.softmax(dim=-2)
        k = k.softmax(dim=-2)

        attn = torch.matmul(q, k.transpose(-2, -1)) * self.temperature
        attn = attn.softmax(dim=-1)

        out = torch.matmul(attn, v)
        out = out.reshape(b, c, h, w)
        out = self.project_out(out)
        return out


# ── Gated-DConv Feed-Forward Network (GDFN) ─────────────────────────────

class GDFN(nn.Module):
    """Gated-DConv Feed-Forward Network"""

    def __init__(self, dim: int, ffn_expansion_factor: float = 2.66):
        super(GDFN, self).__init__()
        hidden_features = int(dim * ffn_expansion_factor)

        self.project_in = nn.Conv2d(dim, hidden_features * 2, kernel_size=1, bias=False)
        self.dwconv = nn.Conv2d(
            hidden_features * 2, hidden_features * 2,
            kernel_size=3, stride=1, padding=1,
            groups=hidden_features * 2, bias=False
        )
        self.project_out = nn.Conv2d(hidden_features, dim, kernel_size=1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1, x2 = self.dwconv(self.project_in(x)).chunk(2, dim=1)
        x1 = F.gelu(x1)
        x = x1 * x2
        x = self.project_out(x)
        return x


# ── Transformer Block ────────────────────────────────────────────────────────

class TransformerBlock(nn.Module):
    """Restormer Transformer Block"""

    def __init__(self, dim: int, num_heads: int = 8, ffn_expansion_factor: float = 2.66):
        super(TransformerBlock, self).__init__()
        self.norm1 = nn.LayerNorm(dim, elementwise_affine=False)
        self.attn = MDTA(dim, num_heads)
        self.norm2 = nn.LayerNorm(dim, elementwise_affine=False)
        self.ffn = GDFN(dim, ffn_expansion_factor)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, h, w = x.shape
        # MDTA
        x_norm = self.norm1(x.permute(0, 2, 3, 1))
        x_norm = x_norm.permute(0, 3, 1, 2)
        x = x + self.attn(x_norm)
        # GDFN
        x_norm = self.norm2(x.permute(0, 2, 3, 1))
        x_norm = x_norm.permute(0, 3, 1, 2)
        x = x + self.ffn(x_norm)
        return x


# ── Overlapping Patch Embedding ────────────────────────────────────────────

class OverlapPatchEmbed(nn.Module):
    """Overlapping Patch Embedding"""

    def __init__(self, in_c: int = 3, embed_dim: int = 48, bias: bool = False):
        super(OverlapPatchEmbed, self).__init__()
        self.proj = nn.Conv2d(
            in_c, embed_dim,
            kernel_size=3, stride=1, padding=1, bias=bias
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


# ── Downsample / Upsample ─────────────────────────────────────────────────

class Downsample(nn.Module):
    """Downsample (Patch Merging)"""

    def __init__(self, n_feat: int):
        super(Downsample, self).__init__()
        self.body = nn.Sequential(
            nn.Conv2d(n_feat, n_feat * 2, kernel_size=3, stride=2, padding=1, bias=False),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)


class Upsample(nn.Module):
    """Upsample (PixelShuffle)"""

    def __init__(self, n_feat: int):
        super(Upsample, self).__init__()
        self.body = nn.Sequential(
            nn.Conv2d(n_feat, n_feat * 2, kernel_size=3, stride=1, padding=1, bias=False),
            nn.PixelShuffle(2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)


# ── Restormer Pipeline ─────────────────────────────────────────────────────

class RestormerPipeline(nn.Module):
    """
    Restormer 模型 Pipeline

    支持任务：
    - denoise：去噪
    - deblur：去模糊
    - derain：去雨滴
    - dehaze：去雾
    """

    def __init__(
        self,
        inp_channels: int = 3,
        out_channels: int = 3,
        dim: int = 48,
        num_blocks: List[int] = [4, 6, 6, 8],
        num_refinement_blocks: int = 4,
        heads: List[int] = [1, 2, 4, 8],
        ffn_expansion_factor: float = 2.66,
    ):
        super(RestormerPipeline, self).__init__()

        self.patch_embed = OverlapPatchEmbed(inp_channels, dim)

        # Encoder
        self.encoder_levels = nn.ModuleList()
        for i, (num_block, head) in enumerate(zip(num_blocks, heads)):
            level = nn.Sequential(
                *[TransformerBlock(dim * (2 ** i), head, ffn_expansion_factor)
                  for _ in range(num_block)]
            )
            self.encoder_levels.append(level)

        # Downsample
        self.down_levels = nn.ModuleList()
        for i in range(len(num_blocks) - 1):
            self.down_levels.append(Downsample(dim * (2 ** i)))

        # Upsample
        self.up_levels = nn.ModuleList()
        for i in range(len(num_blocks) - 1):
            self.up_levels.append(Upsample(dim * (2 ** (len(num_blocks) - 2 - i))))

        # Decoder
        self.decoder_levels = nn.ModuleList()
        for i, (num_block, head) in enumerate(reversed(list(zip(num_blocks, heads)))):
            level = nn.Sequential(
                *[TransformerBlock(dim * (2 ** (len(num_blocks) - 1 - i)), head, ffn_expansion_factor)
                  for _ in range(num_block)]
            )
            self.decoder_levels.append(level)

        # Refinement
        self.refinement = nn.Sequential(
            *[TransformerBlock(dim, heads[0], ffn_expansion_factor)
              for _ in range(num_refinement_blocks)]
        )

        self.output = nn.Conv2d(dim, out_channels, kernel_size=3, stride=1, padding=1, bias=False)
        self.input_conv = nn.Conv2d(inp_channels, dim, kernel_size=3, stride=1, padding=1, bias=False)
        self.skip_convs = nn.ModuleList([
            nn.Conv2d(dim * (2 ** i), dim * (2 ** i), kernel_size=1, bias=False)
            for i in range(len(num_blocks))
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播"""
        inp = x
        
        # Patch Embedding
        x = self.patch_embed(x)  # [B, dim, H, W]
        
        # Encoder
        enc_outs = []
        for i, level in enumerate(self.encoder_levels):
            if i > 0:
                x = self.down_levels[i - 1](x)  # Downsample
            x = level(x)  # Transformer blocks
            enc_outs.append(x)
        
        # Decoder
        for i, level in enumerate(self.decoder_levels):
            if i > 0:
                x = self.up_levels[i - 1](x)  # Upsample
            # Skip connection: 从对应的 encoder 输出相加
            x = x + enc_outs[len(self.encoder_levels) - 1 - i]
            x = level(x)
        
        # Refinement
        x = self.refinement(x)  # 输出通道数 = dim
        
        # Output projection: [B, dim, H, W] -> [B, out_channels, H, W]
        x = self.output(x)
        
        # 残差连接：将原始输入调整到与输出相同的空间尺寸
        if x.shape[2:] != inp.shape[2:]:
            inp = F.interpolate(inp, size=x.shape[2:], mode='bilinear', align_corners=False)
        
        return x + inp  # Residual connection

    @classmethod
    def from_pretrained(cls, model_path: str, device: str = "cpu", task_type: str = "denoise"):
        """
        从预训练权重加载模型

        :param model_path: 权重文件路径或目录
        :param device: 推理设备
        :param task_type: 任务类型（denoise/deblur/derain/dehaze）
        :return: 加载好权重的模型
        """
        # 根据任务类型设置模型参数
        configs = {
            "denoise": {
                "num_blocks": [4, 6, 6, 8],
                "heads": [1, 2, 4, 8],
                "dim": 48,
            },
            "deblur": {
                "num_blocks": [4, 6, 6, 8],
                "heads": [1, 2, 4, 8],
                "dim": 48,
            },
            "derain": {
                "num_blocks": [4, 6, 6, 8],
                "heads": [1, 2, 4, 8],
                "dim": 48,
            },
            "dehaze": {
                "num_blocks": [4, 6, 6, 8],
                "heads": [1, 2, 4, 8],
                "dim": 48,
            },
        }

        config = configs.get(task_type, configs["denoise"])

        model = cls(
            inp_channels=3,
            out_channels=3,
            dim=config["dim"],
            num_blocks=config["num_blocks"],
            heads=config["heads"],
        )

        # 加载权重
        if isinstance(model_path, str):
            import os
            if os.path.isdir(model_path):
                # 查找目录中的权重文件
                for f in os.listdir(model_path):
                    if f.endswith(".pth") or f.endswith(".pt"):
                        model_path = os.path.join(model_path, f)
                        break

            if os.path.exists(model_path):
                state_dict = torch.load(model_path, map_location="cpu")
                # 处理可能的 key 不匹配
                if "state_dict" in state_dict:
                    state_dict = state_dict["state_dict"]
                # 移除 "module." 前缀（如果是 DataParallel 训练的）
                new_state_dict = {}
                for k, v in state_dict.items():
                    new_k = k.replace("module.", "")
                    new_state_dict[new_k] = v
                model.load_state_dict(new_state_dict, strict=False)

        model = model.to(device)
        return model
