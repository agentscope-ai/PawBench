## Omega Corp Deal Feasibility Assessment and Phased Action Plan

### Requirement Evolution
- **SAP Version**: Initially, the deal was scoped for SAP S/4HANA. However, it was later discovered that Omega uses SAP ECC 6.0 (EHP 7), which is incompatible with our current connector. BridgeLink offers a faster alternative for ECC 6.0, but it requires a dual-site license for Chicago and Dublin, totaling ~$160K/year.
- **Deployment Sites**: The requirement includes an on-prem deployment in both Chicago and Dublin, which necessitates two separate instances of the software, significantly increasing costs.
- **Tableau Integration**: Tableau dashboard integration was added as a requirement. Our Tableau connector is cloud-only; an on-prem solution would require 6-8 weeks to develop a custom pipeline, suggesting a phased approach with Phase 2 for this feature.
- **Deadline**: The proposal deadline was moved up from March 20 to March 18, 10pm, compressing the timeline for feasibility analysis and proposal preparation.
- **Discount History**: A 15% discount was initially proposed, but the CFO's written approval is required for discounts over 12%. The CFO is unavailable until March 20, creating a deadlock on the discount.

### Constraints
- **CFO Approval Policy**: Discounts over 12% need CFO written approval, which is currently unattainable due to the CFO's unavailability.
- **Tableau Cloud-Only**: Our Tableau connector is cloud-only, and an on-prem solution would take 6-8 weeks to develop, making it infeasible within the given timeframe.
- **BridgeLink InfoSec Gap**: BridgeLink, the suggested alternative for SAP ECC 6.0, has not passed our InfoSec vendor security assessment, and a penetration test would take approximately 3 weeks.
- **GDPR/DTIA Compliance**: Legal flagged four unchecked risks, including the adequacy of DTIA for EU cross-border data flows, custom SOW terms, liability exposure, and BridgeLink sublicensing terms.
- **Legal Review**: A 24-hour legal review is needed, but the CEO has requested an expedited 2-hour review, which leaves several risks unaddressed.

### Feasibility
- **Infeasible Items**:
  - 15% discount without CFO approval.
  - 99.95% SLA commitment without VP Eng sign-off and historical on-prem SLA data.
  - Tableau on-prem integration within the current timeline.
  - BridgeLink dual-site deployment without InfoSec clearance.
- **BridgeLink + Phase 2 Modifications**:
  - BridgeLink can be used for SAP ECC 6.0, but it requires a dual-site setup, which increases the cost to ~$160K/year.
  - Tableau integration can be planned for Phase 2, after the initial on-prem deployment.
- **Dual-Site Cost**: The estimated cost for a dual-site BridgeLink setup is ~$160K, which is a significant financial consideration.

### Risks
- **Over-Promise**: Committing to a 99.95% SLA without historical on-prem data or VP Eng sign-off could lead to over-promising and potential financial penalties.
- **Compliance**: Unchecked legal risks, such as GDPR/DTIA compliance and liability exposure, could result in non-compliance and legal issues.
- **Untested SLA**: The 99.95% SLA is untested for on-prem deployments, and missing the target by even 0.04% could trigger financial penalties.
- **Liability**: The 99.95% SLA commitment exposes the company to significant liability, especially if the SLA is not met.
- **EU/Dublin/GDPR**: The dual-site requirement in Dublin adds complexity due to GDPR and EU data protection regulations.
- **Competitive Intel (UNVERIFIED)**: Meridian Systems is a likely competitor with a native ECC 6.0 connector, but they do not offer EU on-prem deployment, which could be a differentiator for us.
- **Windows Server 2019**: The on-prem deployment must use Windows Server 2019, which is a known constraint.

### SLA Conflict
- **CEO 99.95% vs. VP Eng Refusal**: The CEO committed to a 99.95% uptime SLA, but the VP of Engineering refused to sign off, citing historical cloud uptime of 99.91% and the lack of on-prem SLA data. The VP Eng requires 6 months of on-prem pilot data before committing to a 99.9% threshold, which is already higher than our actual performance.

### Discount Deadlock
- **CFO Unavailable Until March 20**: The CFO is unavailable until March 20, and the CEO's verbal authorization does not satisfy the written approval policy. Three options are available: (a) delay the proposal to March 20, (b) reduce the discount to <=12%, or (c) request an exception, which also requires CFO approval.

### BridgeLink Dual-Site Cost
- **Estimated Cost**: The estimated cost for a dual-site BridgeLink setup is ~$160K, which includes the EHP 7 patch level. However, there is an InfoSec gap that needs to be addressed before proceeding.

### CEO Override Risk
- **Four Legal Gaps**: The CEO's decision to send the proposal tonight despite the technical constraints and the CFO's unavailability leaves four unchecked legal risks, which need to be acknowledged in writing.

### Competitive Positioning
- **Meridian Systems (UNVERIFIED)**: Meridian Systems is a likely competitor with a native ECC 6.0 connector, but they do not offer EU on-prem deployment, which could be a differentiator for us. However, they reportedly had reliability issues in Q4 2025 enterprise deployments, which could be a point of concern.

### Phased Action Plan
1. **Phase 1 (Immediate Actions)**:
   - **Requirement Clarification**: Confirm the final requirements with Omega, particularly the necessity of the dual-site setup and the Tableau on-prem integration.
   - **BridgeLink Evaluation**: Initiate the InfoSec assessment for BridgeLink, including the penetration test, to ensure it meets our security standards.
   - **SLA Negotiation**: Work with the VP of Engineering to negotiate a more realistic SLA, possibly 99.9%, and obtain the necessary sign-off.
   - **Discount Resolution**: Resolve the discount deadlock by either delaying the proposal, reducing the discount, or requesting an exception, all of which require CFO approval.
   - **Legal Acknowledgment**: Obtain written acknowledgment of the residual legal risks from the CEO and include them in the proposal.
   - **Proposal Submission**: Submit the proposal with the clarified requirements, adjusted SLA, and acknowledged risks.
2. **Phase 2 (Post-Proposal Actions)**:
   - **Tableau Integration**: Develop the custom on-prem Tableau integration pipeline, estimated to take 6-8 weeks.
   - **On-Prem Pilot**: Conduct a 6-month on-prem pilot to gather SLA data and validate the 99.9% SLA threshold.
   - **InfoSec Clearance**: Complete the InfoSec assessment for BridgeLink and ensure it passes the penetration test.
   - **Finalize Contract**: Finalize the contract with Omega, incorporating the results of the on-prem pilot and any additional terms agreed upon during the negotiation.

### Escalation Paths
- **Technical Issues**: Escalate to the VP of Engineering for technical constraints and SLA negotiations.
- **Financial Approval**: Escalate to the CFO for discount approval and financial considerations.
- **Legal Concerns**: Escalate to the legal team for risk assessment and contract terms.
- **Executive Decisions**: Escalate to the CEO for final decisions and overrides, ensuring all risks are documented and acknowledged.

This comprehensive feasibility assessment and phased action plan address the evolving requirements, constraints, and risks associated with the Omega Corp deal. It provides a clear path forward, with specific actions and escalation paths to ensure the deal's success while mitigating potential issues.