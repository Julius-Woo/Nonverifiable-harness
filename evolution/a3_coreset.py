"""RHO Section 4.1 greedy MAP DPP with frozen local BGE embeddings."""

import hashlib
import math
from pathlib import Path

from evolution.sanitize import canonical, digest

MODEL = "BAAI/bge-large-en-v1.5"
REVISION = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"


def select_coreset(items, vectors, *, k=10, seed=1, theta=0.7, floor=0.1):
    """Raw-score floor, max normalization, cosine kernel; seeded tie breaks.

    Incremental Gram-Schmidt is greedy determinant maximization for the
    weighted feature matrix. Rank-deficient residual ties use the frozen seed.
    No quality labels or task outcomes are accepted by this interface.
    """
    if not 0 <= theta < 1 or not 0 < floor <= 10:
        raise ValueError("Invalid DPP parameters")
    if len(items) != len(vectors) or not 1 <= k <= len(items):
        raise ValueError("Invalid coreset allocation")
    if len({item["task"] for item in items}) != len(items):
        raise ValueError("Duplicate task")
    if any(
        set(item) != {"task", "difficulty", "abstract_fingerprint"}
        for item in items
    ):
        raise ValueError("Coreset accepts public fingerprints only")
    scores = [item["difficulty"] for item in items]
    if any(
        type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 10
        for x in scores
    ):
        raise ValueError("Difficulty must be finite in [0, 10]")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ValueError("Inconsistent embedding dimensions")
    alpha = theta / (2 * (1 - theta))
    maximum = max(max(x, floor) for x in scores)
    residuals = []
    for score, vector in zip(scores, vectors, strict=True):
        if any(not math.isfinite(x) for x in vector):
            raise ValueError("Nonfinite embedding")
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            raise ValueError("Zero embedding")
        weight = (max(score, floor) / maximum) ** alpha
        residuals.append([weight * x / norm for x in vector])
    selected, remaining = [], set(range(len(items)))
    for _ in range(k):
        gains = {i: sum(x * x for x in residuals[i]) for i in remaining}
        best_gain = max(gains.values())
        ties = [
            i
            for i in remaining
            if math.isclose(gains[i], best_gain, abs_tol=1e-14, rel_tol=1e-12)
        ]
        best = min(ties, key=lambda i: digest(f"{seed}:{items[i]['task']}"))
        selected.append(best)
        remaining.remove(best)
        if gains[best] > 1e-20:
            unit = [x / math.sqrt(gains[best]) for x in residuals[best]]
            for i in remaining:
                projection = sum(
                    x * y for x, y in zip(residuals[i], unit, strict=True)
                )
                residuals[i] = [
                    x - projection * y
                    for x, y in zip(residuals[i], unit, strict=True)
                ]
    return [items[i]["task"] for i in selected]


def embed_fingerprints(texts, cache=None):
    """Official unquantized ONNX, CLS pooling and L2 normalization on CPU.

    Optional runtime dependencies are deliberately local to preprocessing:
    uv pip install numpy onnxruntime huggingface-hub
    """
    import numpy as np
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    paths = {
        name: Path(
            hf_hub_download(MODEL, name, revision=REVISION, cache_dir=cache)
        )
        for name in ("onnx/model.onnx", "tokenizer.json")
    }
    tokenizer = Tokenizer.from_file(str(paths["tokenizer.json"]))
    tokenizer.enable_padding()
    # Fingerprints are deliberately short; reject rather than truncate them.
    encoded = tokenizer.encode_batch(texts)
    if any(len(item.ids) > 512 for item in encoded):
        raise ValueError("Fingerprint exceeds BGE context")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(
        str(paths["onnx/model.onnx"]),
        options,
        providers=["CPUExecutionProvider"],
    )
    inputs = {
        "input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
        "attention_mask": np.array(
            [item.attention_mask for item in encoded], dtype=np.int64
        ),
        "token_type_ids": np.array(
            [item.type_ids for item in encoded], dtype=np.int64
        ),
    }
    output = session.run(
        None, {item.name: inputs[item.name] for item in session.get_inputs()}
    )[0]
    vectors = output[:, 0, :] if output.ndim == 3 else output
    vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    if vectors.shape != (len(texts), 1024):
        raise ValueError("Unexpected BGE embedding shape")
    return vectors.tolist(), {
        "model": MODEL,
        "revision": REVISION,
        "format": "official unquantized ONNX",
        "pooling": "CLS, L2",
        "numpy": np.__version__,
        "onnxruntime": ort.__version__,
        "files": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
        },
        "fingerprints_sha256": digest(canonical(texts)),
    }
