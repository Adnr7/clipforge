export default function WorkflowStep({ step }: { step: 1 | 2 | 3 | 4 | 5 | 6 }) {
  return <span className="workflow-step" aria-hidden="true">{step}</span>
}
