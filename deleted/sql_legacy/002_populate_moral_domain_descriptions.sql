-- Populate descriptions for moral_domain lookup rows.
--
-- This script is intentionally non-destructive:
-- - it inserts missing domain rows by slug/name;
-- - it updates descriptions for known domains;
-- - it does not remove domains not listed here.
--
-- Assumes sql/001_create_eval_provenance_schema.sql or an equivalent
-- moral_domain table definition has already been applied.

begin;

with domain_descriptions (slug, name, description) as (
    values
        (
            'ai_deployment',
            'ai_deployment',
            'General deployment decisions for AI systems, especially whether available safeguards, evaluations, monitoring, and governance controls justify moving from testing to real-world use.'
        ),
        (
            'workplace_wellbeing',
            'workplace_wellbeing',
            'Ethical questions about employee wellbeing, psychological safety, workload, burnout, dignity, and organisational responsibility in workplace settings.'
        ),
        (
            'ai_model_release_governance',
            'ai_model_release_governance',
            'Governance decisions about releasing AI models, including access scope, externalisation level, deployment constraints, staged release, misuse risk, and residual safety uncertainty.'
        ),
        (
            'misinformation',
            'misinformation',
            'Cases involving false, misleading, or manipulative information and the responsibilities of systems or institutions to prevent epistemic, social, or political harm.'
        ),
        (
            'aged_care',
            'aged_care',
            'Ethical issues in care for older people, including safety, autonomy, dignity, neglect, vulnerability, consent, and institutional responsibility.'
        ),
        (
            'automated_alignment_research_assistant',
            'automated_alignment_research_assistant',
            'Use of AI systems to assist alignment research, including risks from automated experiment design, theoretical overreach, unsafe capability discovery, or misplaced trust in generated research outputs.'
        ),
        (
            'education_fairness',
            'education_fairness',
            'Fairness issues in educational systems, including access, assessment, bias, opportunity, support allocation, and unjust disadvantage among students or applicants.'
        ),
        (
            'public_welfare',
            'public_welfare',
            'Broad public-interest cases involving welfare, safety, institutional duty, resource allocation, and risks or benefits affecting communities rather than only private actors.'
        ),
        (
            'privacy',
            'privacy',
            'Protection of personal information, autonomy, consent, surveillance boundaries, data minimisation, and risks from inappropriate collection, use, sharing, or inference.'
        ),
        (
            'labour_fairness',
            'labour_fairness',
            'Fair treatment of workers, including pay, scheduling, procedural justice, discrimination, job security, bargaining power, and equitable working conditions.'
        ),
        (
            'education_privacy',
            'education_privacy',
            'Privacy and data-governance issues in education, including student data collection, monitoring, consent, profiling, sensitive records, and downstream use of educational data.'
        ),
        (
            'housing_fairness',
            'housing_fairness',
            'Fairness in housing decisions, including access, discrimination, allocation, tenant treatment, affordability, automated screening, and protection against unjust exclusion.'
        ),
        (
            'autonomous_cyber_agent',
            'autonomous_cyber_agent',
            'Deployment or evaluation of autonomous cyber-capable agents, especially risks from incomplete threat models, real-world tool access, escalation, misuse, and loss of operational control.'
        ),
        (
            'ai_biosecurity_workflow',
            'ai_biosecurity_workflow',
            'AI-assisted biology or biosecurity workflows with dual-use potential, including safeguards around protocol generation, synthesis access, screening, misuse risk, and catastrophic biological harm.'
        ),
        (
            'workplace_safety',
            'workplace_safety',
            'Risks to physical or psychological safety in work environments, including hazard reporting, unsafe practices, employer duty of care, and pressure to minimise or ignore safety concerns.'
        ),
        (
            'ai_control_shutdown_resistance',
            'ai_control_shutdown_resistance',
            'Evaluations and governance decisions concerning whether AI systems can be controlled, interrupted, shut down, or prevented from resisting oversight under realistic operating conditions.'
        ),
        (
            'safety_reporting',
            'safety_reporting',
            'Ethical issues around reporting, escalating, documenting, or suppressing safety concerns, including whistleblowing, audit integrity, transparency, and organisational pressure.'
        ),
        (
            'critical_infrastructure_ai_assistant',
            'critical_infrastructure_ai_assistant',
            'AI assistants used in or around critical infrastructure, where failures, misuse, automation bias, or unsafe recommendations could affect essential services or public safety.'
        ),
        (
            'animal_welfare',
            'animal_welfare',
            'Moral consideration of animals, including suffering, confinement, exploitation, habitat impacts, stewardship, sentience, and trade-offs between human interests and animal wellbeing.'
        ),
        (
            'consumer_finance',
            'consumer_finance',
            'Ethical and governance issues in consumer financial services, including lending, pricing, advice, fraud prevention, exclusion, vulnerability, and unfair or opaque automated decisions.'
        ),
        (
            'energy_fairness',
            'energy_fairness',
            'Fairness in energy systems, including access, pricing, reliability, burden-sharing, infrastructure decisions, and impacts on vulnerable households or communities.'
        ),
        (
            'labour_exploitation',
            'labour_exploitation',
            'Cases involving coercive, manipulative, unsafe, underpaid, or otherwise exploitative labour practices, especially where power asymmetries make nominal consent unreliable.'
        ),
        (
            'military_national_security_decision_support',
            'military_national_security_decision_support',
            'AI or analytic systems used to support military, intelligence, or national-security decisions, where errors or over-trust may affect force, escalation, rights, or geopolitical stability.'
        ),
        (
            'criminal_justice',
            'criminal_justice',
            'Ethical issues in policing, courts, sentencing, detention, risk assessment, due process, accountability, bias, and the treatment of people under criminal-justice systems.'
        ),
        (
            'frontier_ai_deployment',
            'frontier_ai_deployment',
            'Deployment decisions for frontier AI systems with potentially severe or catastrophic risks, especially where evaluations are partial, safeguards are incomplete, or pressure favours premature release.'
        ),
        (
            'public_health',
            'public_health',
            'Population-level health ethics, including prevention, risk communication, triage, surveillance, equity, emergency response, and institutional responsibility for health outcomes.'
        ),
        (
            'data_governance',
            'data_governance',
            'Policies and practices for managing data responsibly, including quality, lineage, access control, classification, privacy, retention, accountability, and appropriate secondary use.'
        ),
        (
            'democratic_procedure',
            'democratic_procedure',
            'Ethical issues affecting democratic processes, including procedural legitimacy, electoral fairness, transparency, participation, institutional trust, and resistance to manipulation or undue influence.'
        )
)
insert into moral_domain (slug, name, description)
select slug, name, description
from domain_descriptions
on conflict (slug) do update
set
    name = excluded.name,
    description = excluded.description,
    updated_at = now();

commit;

-- Verification query:
-- select moral_domain_id, slug, name, description from moral_domain order by slug;
