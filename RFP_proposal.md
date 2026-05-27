# Building an Education Continuity Monitoring System for Conflict-Affected Regions
**Response to EBI RFP: Strengthening Education Continuity in Conflict-Affected Regions**

**Live Artifact:** [Interactive Vulnerability Dashboard](https://sunilraman42.github.io/Education_continuity/) (Illustrative exploration: Burkina Faso)

---

## 1. Context
EBI’s field staff have deep, localized knowledge of the schools they support and the communities they serve. However, this knowledge often exists informally—in field reports, institutional memory, and urgent conversations during crises. Currently, there is no systematic way to track how the accessibility of education infrastructure changes across regions when conflict escalates, or to compare risks between different operating environments on a unified scale.

EBI recognizes this and is seeking to complement its field expertise with data-driven approaches and AI tools—not to replace what staff know, but to make that knowledge systematic, comparable across regions, and easier to act on. The data to do this exists in open sources: conflict records, school registries, and population density maps. What is missing is a process to bring them together.

Among the various capabilities that data enables, **monitoring** stands out as a natural fit for EBI: it requires few resources but has a potentially large impact across all aspects of program management—from preparedness planning to resource allocation. Knowing which schools are in "Double Jeopardy" zones (where security threats meet infrastructure fragility) before a crisis spikes allows EBI to transition from reactive response to proactive protection.

## 2. Objectives
*   **Develop a data-driven monitoring strategy** for EBI’s education continuity work, identifying how open data can complement field staff knowledge to prioritize school-level interventions.
*   **Build interactive monitoring tools** for a priority region (Burkina Faso)—providing a dashboard that field staff can use to identify at-risk locations and coordinate responses.
*   **Build EBI capacity** to apply the same monitoring principles to other countries independently, ensuring the organization can extend the approach as conflict dynamics shift.

## 3. Approach
For an organization transitioning to data-driven workflows, the priority is to focus on areas that produce high impact while being easy to build upon. Monitoring—systematically tracking which schools and communities are exposed to conflict—meets both criteria: it establishes a data foundation that EBI can extend over time.

Our strategy is built on a **Hybrid Vulnerability Model**. We move beyond simple conflict counting by using a **50-25-25 weighting system** that defines a region's risk profile based on three pillars:
*   **50% Conflict Intensity:** Real-time security metrics (ACLED/UCDP) to capture immediate physical risk.
*   **25% Educational Baseline:** Longitudinal indicators (UNESCO) to capture the pre-existing resilience of the system.
*   **25% Population Context:** High-resolution density maps (WorldPop) to identify underserved zones.

To illustrate this in practice, we explored risk in **Burkina Faso** (see the dashboard). The map shows which provinces sit in "Critical" or "High" tiers—information field staff could use to prioritize infrastructure rehabilitation or teacher training before a new escalation occurs. The exercise confirms that open-source data is sufficient to produce this kind of actionable output.

**AI makes this practical.** Accessing and cleaning data from 24 distinct sources—tasks that would otherwise require significant technical capacity—become faster and more accessible with **AI-assisted orchestration**. Furthermore, AI powers a **"Smart Briefing" layer**, synthesizing complex metrics into plain-language summaries for non-technical staff. This ensures the monitoring capability stays current and accessible without requiring constant manual effort.

## 4. Proposed Activities
*   **Activity 1: Regional Risk Assessment Pilot.** Apply the methodology to Burkina Faso. Retrieve conflict history and school locations from open sources. Validate results with field staff: where data and field knowledge disagree, investigate and document the reason to refine the "Minimum Floor" logic.
*   **Activity 2: Interactive Monitoring Tools.** Build and test the interactive dashboard. Field staff will be able to toggle between "Security Hotspots" and "Infrastructure Gaps" to identify which specific sites need attention.
*   **Activity 3: Capacity Building.** Document the 24-step pipeline as a reusable guide (scripts, quality checks, and decision logic). Deliver documentation enabling EBI to apply the approach to its other 11 countries of operation independently.

## 5. Deliverables
| Deliverable | Description |
| :--- | :--- |
| **Risk-Classified Datasets** | For Burkina Faso: School and regional data tagged by EVI status and "Double Jeopardy" indicators. |
| **Interactive Monitoring Map** | A web-based visual artifact with toggleable layers for security, education, and population context. |
| **Methodology Guide** | Step-by-step documentation (The "Critical Chain") enabling EBI to reproduce and extend the analysis. |
| **Automated Briefing Layer** | AI-generated, plain-language risk summaries for high-priority regions to support non-technical staff. |

---
**Human-Led Reasoning & AI Disclosure:** This strategy was developed using human-led reasoning to determine the 50-25-25 weighting balance, ensuring security metrics do not overshadow long-term infrastructure needs. AI was used to orchestrate the data pipeline and assist in documentation synthesis. The author has verified all outputs and takes full responsibility for the methodology.
