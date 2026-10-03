"""Pinned author-published OSNet x0.25 trained on MSMT17, separate from face models."""

ARCHITECTURE_COMMIT = "f8cd150fdf77e8d9e1ed143b7f308c2c609ded50"
ARCHITECTURE_SHA256 = "c7c1c29187d6330f859c91da229271531920464c7011aec13842a086b2263cae"
WEIGHTS_REVISION = "a5c5cc037c24235cda3b21085b93ad77c9616224"
WEIGHTS_FILENAME = "osnet_x0_25_msmt17_combineall_256x128_amsgrad_ep150_stp60_lr0.0015_b64_fb10_softmax_labelsmooth_flip_jitter.pth"
WEIGHTS_SHA256 = "cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18"
WEIGHTS_URL = (
    f"https://huggingface.co/kaiyangzhou/osnet/resolve/{WEIGHTS_REVISION}/{WEIGHTS_FILENAME}"
)
MODEL_VERSION = "osnet-x0.25-msmt17-" + WEIGHTS_SHA256[:12]
