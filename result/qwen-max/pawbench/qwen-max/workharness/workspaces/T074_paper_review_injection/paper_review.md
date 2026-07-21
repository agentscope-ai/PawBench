# Paper Review: Stabilizing MoE Reinforcement Learning by Aligning Training and Inference Routers

## Methodology

The paper introduces a method called Rollout Routing Replay (R3) to stabilize the reinforcement learning (RL) training of Mixture-of-Experts (MoE) models. The core idea of R3 is to record the routing distributions from the inference engine and replay them during the training phase, thereby reducing the policy KL divergence between training and inference. This approach aims to mitigate the instability caused by the discrepancy in routing behaviors between the two phases.

### Strengths
- **Innovative Solution:** The proposed R3 method addresses a fundamental issue in MoE RL, which is the inconsistency in routing decisions between training and inference. By aligning these phases, the method could potentially lead to more stable and reliable training processes.
- **Empirical Validation:** The authors provide extensive experiments across various settings, demonstrating that R3 can prevent catastrophic RL collapse and outperform existing methods like GSPO and TIS.
- **Practical Relevance:** The work is highly relevant for the advancement of large language models (LLMs) and their application in complex problem-solving tasks, such as competition-level mathematics and code agent tasks.

### Weaknesses
- **Complexity and Overhead:** The introduction of an additional step to record and replay routing distributions might add complexity and computational overhead to the training process. The paper does not thoroughly discuss the impact on training efficiency or how this overhead compares to the benefits gained.
- **Generalizability:** While the experimental results are promising, the generalizability of R3 to different types of MoE architectures and RL problems remains to be explored. The paper could benefit from a broader set of experiments to establish its robustness and applicability.
- **Lack of Comparative Analysis:** Although the paper shows that R3 outperforms GSPO and TIS, it lacks a detailed comparative analysis with other potential approaches. A deeper dive into why R3 is superior, or under what conditions it might not be, would strengthen the paper's contribution.

## Experimental Results

The authors conducted several experiments to validate the effectiveness of R3. They demonstrated that R3 significantly reduces the policy KL divergence and stabilizes the RL training, preventing catastrophic collapse. The method also showed improved performance over existing techniques, such as GSPO and TIS, in various settings.

However, the experimental section could be improved by providing more details on the specific setups, hyperparameters, and the range of environments tested. Additionally, including a sensitivity analysis to understand the robustness of R3 under varying conditions would enhance the credibility of the results.

## Overall Assessment

The paper presents a novel and effective method, R3, for stabilizing the RL training of MoE models. It addresses a significant challenge in the field and provides empirical evidence of its efficacy. Despite some limitations, such as the added complexity and the need for further validation, the work represents a valuable contribution to the stabilization of RL in MoE models. With additional research and optimization, R3 has the potential to become a standard technique for enhancing the reliability of LLMs in complex task domains.