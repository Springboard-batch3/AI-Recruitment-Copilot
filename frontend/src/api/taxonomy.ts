import { apiFetch } from "./client";

export interface OccupationSearchItem {
  conceptId: string;
  onetSocCode: string;
  canonicalTitle: string;
  matchedAlias?: string | null;
  source: string;
  sourceVersion: string;
  entityType?: string;
  datasetVersion?: string;
}

export interface SkillSearchItem {
  conceptId: string;
  canonicalLabel: string;
  matchedAlias?: string | null;
  category?: string | null;
  source: string;
  sourceFamily?: "essential" | "transferable" | "software" | string;
  sourceVersion?: string | null;
  externalId?: string | null;
  entityType?: string;
}

export interface OccupationSkillSuggestion {
  conceptId: string;
  canonicalLabel: string;
  category?: string | null;
  source: string;
  sourceFamily: "essential" | "transferable" | "software" | string;
  externalId?: string | null;
  hotTechnology: boolean;
  inDemand: boolean;
  importanceValue?: number | null;
  levelValue?: number | null;
  recommendSuppress: boolean;
  notRelevant: boolean;
}

export async function searchOccupations(query: string, limit: number = 10): Promise<OccupationSearchItem[]> {
  const trimmed = query.trim();
  if (!trimmed) return [];
  const params = new URLSearchParams({
    q: trimmed,
    limit: String(limit),
  });
  return apiFetch<OccupationSearchItem[]>(`/api/v1/taxonomy/occupations?${params.toString()}`);
}

export async function searchSkills(query: string, limit: number = 10): Promise<SkillSearchItem[]> {
  const trimmed = query.trim();
  if (!trimmed) return [];
  const params = new URLSearchParams({
    q: trimmed,
    limit: String(limit),
  });
  return apiFetch<SkillSearchItem[]>(`/api/v1/taxonomy/skills?${params.toString()}`);
}

export async function getOccupationSkillSuggestions(conceptId: string, limit: number = 30): Promise<OccupationSkillSuggestion[]> {
  const params = new URLSearchParams({
    limit: String(limit),
  });
  return apiFetch<OccupationSkillSuggestion[]>(`/api/v1/taxonomy/occupations/${conceptId}/skills?${params.toString()}`);
}

