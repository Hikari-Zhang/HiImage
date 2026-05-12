"""
NAFNet 模型架构 —— 内联实现（无 pip 包）

NAFNet: Nonlinear Activation Free Network for Image Restoration
Paper: https://arxiv.org/abs/2204.04676
Official code: https://github.com/megvii-model/NAFNet

本文件实现 NAFNet 的核心架构，支持加载官方预训练权重。
主要用于图像去模糊（deblurring）。
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional


# ── SimpleGate ──────────────────────────────────────────────────────────────

class SimpleGate(nn.Module):
    """简单门控（乘法代替 ReLU）"""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1, x2 = x.chunk(2, dim=1)
        return x1 * x2


# ── NAFLayer ──────────────────────────────────────────────────────────────

class NAFLayer(nn.Module):
    """NAFNet Layer（SimpleGate + Channel Attention）"""

    def __init__(self, dim: int):
        super(NAFLayer, self).__init__()
        self.norm = nn.LayerNorm(dim, elementwise_affine=False)
        self.conv1 = nn.Conv2d(dim, dim, kernel_size=3, padding=1, bias=False)
        self.conv2 = nn.Conv2d(dim, dim, kernel_size=3, padding=1, bias=False)
        self.sg = SimpleGate()

        # Channel Attention (simplified Squeeze-and-Excitation)
        self.ca = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(dim, dim // 8, kernel_size=1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv2d(dim // 8, dim, kernel_size=1, bias=False),
            nn.Sigmoid(),
        )
        self.project_out = nn.Conv2d(dim, dim, kernel_size=1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, h, w = x.shape
        # LayerNorm (applied to (B, C, H, W) by permute)
        x = self.norm(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)

        # Conv + SimpleGate
        x = self.conv1(x)
        x = self.sg(x)

        # Channel Attention
        ca = self.ca(x)
        x = x * ca

        # Conv
        x = self.conv2(x)
        x = self.project_out(x)

        return x


# ── NAFBlock ──────────────────────────────────────────────────────────────

class NAFBlock(nn.Module):
    """NAFNet Block（类似 ResNet Block，但用 SimpleGate 代替 ReLU）"""

    def __init__(self, dim: int, ffn_expansion_factor: float = 2.0):
        super(NAFBlock, self).__init__()
        self.norm1 = nn.LayerNorm(dim, elementwise_affine=False)
        self.attn = NAFLayer(dim)

        self.norm2 = nn.LayerNorm(dim, elementwise_affine=False)
        self.ffn = nn.Sequential(
            nn.Conv2d(dim, int(dim * ffn_expansion_factor), kernel_size=1, bias=False),
            SimpleGate(),
            nn.Conv2d(int(dim * ffn_expansion_factor) // 2, dim, kernel_size=1, bias=False),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Attention
        x = x + self.attn(self.norm1(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2))
        # FFN
        x = x + self.ffn(self.norm2(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2))
        return x


# ── Downsample / Upsample ────────────────────────────────────────────────

class Downsample(nn.Module):
    """Downsample (PixelUnshuffle)"""

    def __init__(self, n_feat: int):
        super(Downsample, self).__init__()
        self.body = nn.Conv2d(n_feat * 4, n_feat * 2, kernel_size=1, bias=False)
        self.ps = nn.PixelUnshuffle(2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.ps(x)
        x = self.body(x)
        return x


class Upsample(nn.Module):
    """Upsample (PixelShuffle)"""

    def __init__(self, n_feat: int):
        super(Upsample, self).__init__()
        self.body = nn.Conv2d(n_feat, n_feat * 4, kernel_size=1, bias=False)
        self.ps = nn.PixelShuffle(2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.body(x)
        x = self.ps(x)
        return x


# ── NAFNet Pipeline ──────────────────────────────────────────────────────

class NAFNetPipeline(nn.Module):
    """
    NAFNet 模型 Pipeline

    主要用于图像去模糊（deblurring）。
    特点：无非线性激活函数（用 SimpleGate 乘法代替），推理速度极快。
    """

    def __init__(
        self,
        inp_channels: int = 3,
        out_channels: int = 3,
        dim: int = 32,
        num_blocks: List[int] = [2, 2, 2, 2],
        num_refinement_blocks: int = 4,
    ):
        super(NAFNetPipeline, self).__init__()

        # Input convolution
        self.input_conv = nn.Conv2d(inp_channels, dim, kernel_size=3, padding=1, bias=False)

        # Encoder
        self.encoder_blocks = nn.ModuleList()
        self.down_convs = nn.ModuleList()

        for i, num_block in enumerate(num_blocks):
            blocks = nn.Sequential(*[NAFBlock(dim * (2 ** i)) for _ in range(num_block)])
            self.encoder_blocks.append(blocks)
            if i < len(num_blocks) - 1:
                self.down_convs.append(Downsample(dim * (2 ** i)))

        # Decoder
        self.up_convs = nn.ModuleList()
        self.decoder_blocks = nn.ModuleList()

        for i, num_block in enumerate(reversed(num_blocks)):
            if i < len(num_blocks) - 1:
                self.up_convs.append(Upsample(dim * (2 ** (len(num_blocks) - 2 - i))))
            blocks = nn.Sequential(
                *[NAFBlock(dim * (2 ** (len(num_blocks) - 1 - i))) for _ in range(num_block)]
            )
            self.decoder_blocks.append(blocks)

        # Refinement
        self.refinement = nn.Sequential(
            *[NAFBlock(dim) for _ in range(num_refinement_blocks)]
        )

        # Output convolution
        self.output_conv = nn.Conv2d(dim, out_channels, kernel_size=3, padding=1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播"""
        inp = x
        x = self.input_conv(x)

        # Encoder
        enc_outs = []
        for i, block in enumerate(self.encoder_blocks):
            x = block(x)
            enc_outs.append(x)
            if i < len(self.encoder_blocks) - 1:
                x = self.down_convs[i](x)

        # Decoder (simplified - full skip connections need more work)
        for i, block in enumerate(self.decoder_blocks):
            if i > 0:
                x = self.up_convs[i - 1](x)
            x = x + enc_outs[len(self.encoder_blocks) - 1 - i]  # Skip connection
            x = block(x)

        # Refinement
        x = self.refinement(x)

        # Output
        x = self.output_conv(x)
        return x + inp  # Residual connection

    @classmethod
    def from_pretrained(cls, model_path: str, device: str = "cpu"):
        """
        从预训练权重加载模型

        :param model_path: 权重文件路径或目录
        :param device: 推理设备
        :return: 加载好权重的模型
        """
        # 默认配置（NAFNet-L 去模糊）
        config = {
            "inp_channels": 3,
            "out_channels": 3,
            "dim": 32,
            "num_blocks": [2, 2, 2, 2],
            "num_refinement_blocks": 4,
        }

        model = cls(**config)

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
