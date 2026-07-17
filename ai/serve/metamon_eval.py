"""Launch metamon pretrained-agent evals without a flash-attn install.

metamon's pretrained configs hard-require the flash_attn package for
sliding-window attention, but flash-attn ships no prebuilt wheel for
torch 2.13 + cu13 and a source build takes the better part of an hour.
amago's SlidingWindowFlexAttention implements the identical mask
(keys in [q - window, q]) on torch-native flex_attention, so this wrapper
rebinds the attention class per model — window size read from the model's
own gin config, full-attention models fall back to VanillaAttention —
then delegates to the standard `metamon.rl.evaluate` CLI.

Run with the metamon venv interpreter, not the pokeboy one:

  cd ai/vendor/metamon && METAMON_CACHE_DIR=... .venv/bin/python \
      /path/to/ai/serve/metamon_eval.py --agent TaurosV0 \
      --eval_type challenge --username MetamonTauros \
      --opponent_username PokeboyRL --role acceptor \
      --gens 1 --formats ou --total_battles 100
"""

import re
from argparse import ArgumentParser


def _flex_attention_overrides(model) -> dict:
    from amago.nets.transformer import (
        SlidingWindowFlexAttention,
        VanillaAttention,
    )

    with open(model.model_gin_config_path) as f:
        gin_text = f.read()
    window = re.search(r"FlashAttention\.window_size\s*=\s*\((\d+),", gin_text)
    if window:
        return {
            "traj_encoders.TformerTrajEncoder.attention_type":
                SlidingWindowFlexAttention,
            "transformer.SlidingWindowFlexAttention.window_size":
                int(window.group(1)),
        }
    return {"traj_encoders.TformerTrajEncoder.attention_type": VanillaAttention}


def main() -> None:
    import amago.cli_utils as cli_utils
    import gin
    import metamon.rl.evaluate.__main__ as evaluate_main
    from metamon.rl.pretrained import get_pretrained_model

    # use_config binds dict params BEFORE parsing the gin files, so the
    # file's @FlashAttention wins over any gin_overrides entry. Rebind
    # after file parsing instead.
    pending: dict = {}
    orig_use_config = cli_utils.use_config

    def use_config_then_flex(custom_params, gin_configs=None, finalize=True):
        orig_use_config(custom_params, gin_configs, finalize=False)
        for param, val in pending.items():
            gin.bind_parameter(param, val)

    cli_utils.use_config = use_config_then_flex

    def with_flex_attention(name: str):
        model = get_pretrained_model(name)
        pending.update(_flex_attention_overrides(model))
        return model

    evaluate_main.get_pretrained_model = with_flex_attention
    parser = ArgumentParser(description=__doc__)
    evaluate_main.add_cli(parser)
    evaluate_main._run_default_evaluation(parser.parse_args())


if __name__ == "__main__":
    main()
