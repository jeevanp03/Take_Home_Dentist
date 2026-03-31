---
name: react-ts
description: Build React components and applications with TypeScript. Use when user wants to create interactive UI, dashboards, or web apps with React and TypeScript.
---

# React + TypeScript Development

## Stack

- React 18+ with TypeScript
- Vite for bundling
- Tailwind CSS for styling
- Recharts or Chart.js for data visualization
- Motion (framer-motion) for animations

## Conventions

- Functional components only, no class components
- Use hooks (useState, useEffect, useMemo, useCallback)
- Type all props with interfaces, not `type` aliases for component props
- Colocate types with components unless shared
- File naming: `ComponentName.tsx`, `useHookName.ts`, `utils.ts`

## Project Setup

```bash
npm create vite@latest dashboard -- --template react-ts
cd dashboard
npm install
npm install tailwindcss @tailwindcss/vite recharts framer-motion lucide-react
```

## Component Patterns

```tsx
interface Props {
  data: DataPoint[];
  onSelect: (id: string) => void;
}

export function ChartPanel({ data, onSelect }: Props) {
  const filtered = useMemo(() => data.filter(d => d.valid), [data]);
  return <div className="p-4">{/* ... */}</div>;
}
```

## Data Visualization

- Use Recharts for charts (BarChart, LineChart, PieChart, RadarChart)
- Use HTML tables with Tailwind for data tables
- Embed analysis data as JSON constants or fetch from static files
- Color scheme should match physician colors: blue/orange/green
