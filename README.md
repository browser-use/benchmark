# Browser Use Benchmark

## Quick Start

<!-- TODO -->

## About the Benchmark

The benchmark consists of 100 tasks drawn from established open-source benchmarks and custom challenges:

| Source | Percentage | Description |
|--------|------------|-------------|
| Custom | 20% | Custom page interaction challenges |
| WebBench | 20% | Hardest tasks from WebBench (read section) |
| OMI2W-2 | 20% | Hardest tasks from OMI2W-2 |
| GAIA | 20% | Hardest tasks from GAIA (web based) |
| BrowseComp | 20% | Hardest tasks from BrowseComp |

WebBench, Mind2Web 2, and BrowseComp are released under the MIT license. GAIA has no explicit license; to comply with its data policies, we only include tasks from the "fully public" validation split, and all tasks are base64 encoded to prevent data contamination.

Tasks were hand-selected for difficulty and verified to be achievable. Each task has been validated  to confirm it can be completed successfully.

**Important:** The task set is stored in base64 encoding to prevent web scrapers from inadvertently including the tasks in LLM training data. Please do not publish the tasks in plaintext or use them in model training data.

### Task Format

When decoded, the task file contains a JSON array. Each task has three fields:

| Field | Description |
|-------|-------------|
| `task_id` | Unique identifier for the task |
| `confirmed_task` | The task instruction to execute |
| `category` | Source benchmark category |
| `answer` | (optional) Ground truth answer if applicable |

## Configuration

<!-- TODO -->

## Running the Benchmark

<!-- TODO -->

## Results

<!-- TODO -->

## Visualizations

<!-- TODO -->

## Adding a New Framework

<!-- TODO -->

## Attributions

This benchmark includes tasks from the following open-source benchmarks:

### WebBench
MIT License | https://webbench.ai/
```bibtex
@misc{webbench2025,
  title = {WebBench: AI Web Browsing Agent Benchmark},
  author = {{Halluminate and Skyvern}},
  year = {2025},
  note = {\url{https://webbench.ai/}},
}
```

### Mind2Web 2 (OMI2W-2)
MIT License | https://openreview.net/forum?id=AUaW6DS9si
```bibtex
@inproceedings{
    gou2025mind2web2,
    title={Mind2Web 2: Evaluating Agentic Search with Agent-as-a-Judge},
    author={Boyu Gou and Zanming Huang and Yuting Ning and Yu Gu and Michael Lin and Botao Yu and Andrei Kopanev and Weijian Qi and Yiheng Shu and Jiaman Wu and Chan Hee Song and Bernal Jimenez Gutierrez and Yifei Li and Zeyi Liao and Hanane Nour Moussa and TIANSHU ZHANG and Jian Xie and Tianci Xue and Shijie Chen and Boyuan Zheng and Kai Zhang and Zhaowei Cai and Viktor Rozgic and Morteza Ziyadi and Huan Sun and Yu Su},
    booktitle={The Thirty-ninth Annual Conference on Neural Information Processing Systems Datasets and Benchmarks Track},
    year={2025},
    url={https://openreview.net/forum?id=AUaW6DS9si}
}
```

### BrowseComp
MIT License | https://cdn.openai.com/pdf/5e10f4ab-d6f7-442e-9508-59515c65e35d/browsecomp.pdf
```bibtex
@techreport{wei2025browsecomp,
  author = {Jason Wei and Zhiqing Sun and Spencer Papay and Scott McKinney and Jeffrey Han and Isa Fulford and Hyung Won Chung and Alex Tachard Passos and William Fedus and Amelia Glaese},
  title = {BrowseComp: A Simple Yet Challenging Benchmark for Browsing Agents},
  institution = {OpenAI},
  year = {2025},
  month = {April},
  url = {https://cdn.openai.com/pdf/5e10f4ab-d6f7-442e-9508-59515c65e35d/browsecomp.pdf},
  note = {arXiv:2504.12516}
}
```

### GAIA
No license (public validation split only) | https://huggingface.co/datasets/gaia-benchmark/GAIA
```bibtex
@misc{mialon2023gaia,
  title={GAIA: a benchmark for General AI Assistants}, 
  author={Grégoire Mialon and Clémentine Fourrier and Craig Swift and Thomas Wolf and Yann LeCun and Thomas Scialom},
  year={2023},
  eprint={2311.12983},
  archivePrefix={arXiv},
  primaryClass={cs.CL}
}
```
