"""Prove CUDA execution with a PyTorch operation and profiled ONNX MatMul."""

import argparse
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper

ROOT = Path(__file__).resolve().parents[1]


def build_smoke_model() -> bytes:
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["a", "b"], ["c"], name="smoke_matmul")],
        "cctv_cuda_smoke",
        [
            helper.make_tensor_value_info("a", TensorProto.FLOAT, [64, 64]),
            helper.make_tensor_value_info("b", TensorProto.FLOAT, [64, 64]),
        ],
        [helper.make_tensor_value_info("c", TensorProto.FLOAT, [64, 64])],
    )
    model = helper.make_model(
        graph, producer_name="cctv-search", opset_imports=[helper.make_opsetid("", 17)]
    )
    model.ir_version = 10
    onnx.checker.check_model(model)
    return model.SerializeToString()


def check(device: str = "cuda", allow_cpu_fallback: bool = False) -> dict:
    import onnxruntime as ort
    import torch

    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "requested_device": device,
        "allow_cpu_fallback": allow_cpu_fallback,
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version(),
        "onnxruntime_version": ort.__version__,
        "available_providers": ort.get_available_providers(),
        "status": "failed",
        "actual_device": None,
        "cuda_node_count": 0,
    }
    cuda_available = torch.cuda.is_available()
    report["cuda_available"] = cuda_available
    if cuda_available:
        properties = torch.cuda.get_device_properties(0)
        report.update(gpu_name=properties.name, vram_mb=properties.total_memory // 1024**2)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            report["driver_version"] = result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    actual = device
    if device == "cuda" and not cuda_available:
        if not allow_cpu_fallback:
            report["reason"] = "PyTorch CUDA unavailable; CPU fallback disabled"
            return report
        actual = "cpu"
    try:
        tensor = torch.ones((64, 64), device=actual)
        product = tensor @ tensor
        if actual == "cuda":
            torch.cuda.synchronize()
        assert product.device.type == actual and product[0, 0].item() == 64.0
        report["pytorch_operation"] = "passed"
        ort.preload_dlls(cuda=True, cudnn=True, msvc=False)
        with tempfile.TemporaryDirectory(prefix="cctv-gpu-") as directory:
            options = ort.SessionOptions()
            options.enable_profiling = True
            options.profile_file_prefix = str(Path(directory) / "profile")
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if actual == "cuda"
                else ["CPUExecutionProvider"]
            )
            session = ort.InferenceSession(
                build_smoke_model(), sess_options=options, providers=providers
            )
            report["session_providers"] = session.get_providers()
            array = np.ones((64, 64), dtype=np.float32)
            for _ in range(3):
                output = session.run(None, {"a": array, "b": array})[0]
                np.testing.assert_allclose(output, array @ array, rtol=1e-5, atol=1e-5)
            profile = json.loads(Path(session.end_profiling()).read_text())
            cuda_nodes = [
                event
                for event in profile
                if event.get("cat") == "Node"
                and event.get("args", {}).get("provider") == "CUDAExecutionProvider"
            ]
            report["cuda_node_count"] = len(cuda_nodes)
            if actual == "cuda" and not cuda_nodes:
                raise RuntimeError("ONNX MatMul did not execute on CUDAExecutionProvider")
        report["onnx_operation"] = "passed"
        report["actual_device"] = actual
        report["status"] = "passed" if device == actual else "cpu_fallback"
    except Exception as exc:
        report["reason"] = f"GPU smoke check failed: {type(exc).__name__}"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--allow-cpu-fallback", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data/reports/gpu.json")
    args = parser.parse_args()
    report = check(args.device, args.allow_cpu_fallback)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    # A requested CUDA run that fell back to CPU is not a successful GPU check.
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
