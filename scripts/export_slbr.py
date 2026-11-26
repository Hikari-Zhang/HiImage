"""Export the official SLBR CLWD checkpoint from a local upstream checkout."""
import argparse
import hashlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace


CHECKPOINT_SHA256 = "be5c0a89513803c8ce56b1768e0c6296900f6ef76df8cbda12900324f9b1caf9"


def export(source: Path, checkpoint: Path, output: Path) -> None:
    import numpy as np
    import onnxruntime as ort
    import torch

    modules = source / "src"
    if not (modules / "networks" / "resunet.py").is_file():
        raise ValueError("--source must point to the upstream SLBR checkout")
    with checkpoint.open("rb") as weights:
        if hashlib.file_digest(weights, "sha256").hexdigest() != CHECKPOINT_SHA256:
            raise ValueError("Checkpoint does not match the official SLBR CLWD weights")
        weights.seek(0)
        state = torch.load(weights, map_location="cpu", weights_only=True)["state_dict"]

    # Expose only the inference modules, avoiding the upstream training entry points.
    for name, path in (("src", modules), ("src.networks", modules / "networks"), ("src.utils", modules / "utils")):
        package = ModuleType(name)
        package.__path__ = [str(path.resolve())]
        sys.modules[name] = package
    from src.networks.methods import slbr

    class ResidualModel(torch.nn.Module):
        def __init__(self, network):
            super().__init__()
            self.network = network

        def forward(self, image):
            backgrounds, masks, _ = self.network(image)
            return (backgrounds[0] - image) * masks[0]

    torch.set_num_threads(4)
    network = slbr(args=SimpleNamespace(
        k_center=2, k_refine=3, k_skip_stage=3, mask_mode="res", bg_mode="res_mask",
        sim_metric="cos", project_mode="simple", use_refine=True,
    )).eval()
    network.load_state_dict({name.removeprefix("module."): value for name, value in state.items()}, strict=True)
    model = ResidualModel(network).eval()
    example = torch.rand(1, 3, 512, 512, generator=torch.Generator().manual_seed(42))

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="slbr-export-", dir=output.parent) as directory:
        temporary = Path(directory) / output.name
        torch.onnx.export(model, example, str(temporary), input_names=["image"], output_names=["residual"],
                          opset_version=17, dynamo=False)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = ort.InferenceSession(str(temporary), options, providers=["CPUExecutionProvider"])
        actual, = session.run(["residual"], {"image": example.numpy()})
        with torch.inference_mode():
            expected = model(example).numpy()
        np.testing.assert_allclose(actual, expected, atol=0.002, rtol=0.001)
        del session
        temporary.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local official SLBR source checkout")
    parser.add_argument("--checkpoint", type=Path, required=True, help="Official CLWD model_best.pth.tar")
    parser.add_argument("--output", type=Path, default=Path("models/slbr/slbr-clwd-512.onnx"))
    args = parser.parse_args()
    export(args.source, args.checkpoint, args.output)
    print(f"Installed {args.output.resolve()}")
