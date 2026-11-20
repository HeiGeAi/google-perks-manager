"""Built-in deployment recipes and their Colab compatibility rules."""

from .colab import GPUS, ProviderError


H3_GPUS = ("A100", "G4", "H100", "L4", "T4")
H3_REGIONS = frozenset({
    "asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2",
    "asia-southeast1", "asia-southeast2", "asia-south1", "asia-south2",
    "australia-southeast1", "southamerica-east1", "europe-west6", "me-west1",
})
PROJECTS = {
    "h3": {"name": "MiniMax H3 + ComfyUI", "gpus": H3_GPUS, "recommended": "A100", "port": 18898,
           "recipe": "h3.json", "license": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"},
    "comfy": {"name": "ComfyUI", "gpus": GPUS, "recommended": "L4", "port": 18898,
              "recipe": "wan22.json", "license": "https://github.com/Comfy-Org/ComfyUI/blob/v0.44.2/LICENSE"},
    "wan": {"name": "Wan2.2 TI2V 5B", "gpus": ("L4", "A100", "H100", "G4"), "recommended": "L4", "port": 18898,
            "recipe": "wan22.json", "license": "https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B"},
    "breeze": {"name": "Breeze TTS 2", "gpus": ("L4", "A100", "H100", "G4"), "recommended": "L4", "port": None,
               "recipe": "breeze.json", "license": "https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE"},
    "qwen": {"name": "Qwen3 0.6B", "gpus": ("CPU",), "recommended": "CPU", "port": 18899,
              "recipe": "qwen06.json", "license": "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/blob/main/LICENSE"},
}


def project_for(spec):
    if spec["kind"] == "deploy":
        return spec["project"]
    if spec.get("engine") == "MiniMax H3":
        return "h3"
    return "breeze" if spec["kind"] == "tts" else "wan"


def require_supported_runtime(project, gpu, region):
    if gpu not in PROJECTS[project]["gpus"]:
        raise ProviderError("实际分配的算力与所选项目不兼容。", 409)
    if project == "h3" and region not in H3_REGIONS:
        raise ProviderError("H3 无法在这个机房运行，或机房尚未核验。请查看 MiniMax H3 社区许可证。", 409)
