// src/components/ToolBadge.jsx
import React from 'react';
import { Search, Database, MessageCircle, Bot, SearchX } from 'lucide-react';

export default function ToolBadge({ tool }) {
  const tools = {
    rag: { label: 'RAG', icon: Database, color: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20' },
    web_search: { label: 'Web Search', icon: Search, color: 'bg-amber-500/10 text-amber-400 border-amber-500/20' },
    direct: { label: 'Direct LLM', icon: Bot, color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' },
    greeting: { label: 'Greeting', icon: MessageCircle, color: 'bg-blue-500/10 text-blue-400 border-blue-500/20' },
    no_results: { label: 'No Matches', icon: SearchX, color: 'bg-rose-500/10 text-rose-400 border-rose-500/20' },
  };

  const config = tools[tool] || tools.direct;
  const Icon = config.icon;

  return (
    <span className={`inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${config.color}`}>
      <Icon className="w-3 h-3" />
      <span>{config.label}</span>
    </span>
  );
}
