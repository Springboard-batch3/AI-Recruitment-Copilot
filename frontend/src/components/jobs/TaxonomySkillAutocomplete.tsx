import React, { useState, useEffect, useRef } from "react";
import { searchSkills, SkillSearchItem } from "../../api/taxonomy";
import { Icon } from "../Icon";

interface TaxonomySkillAutocompleteProps {
  initialValue?: string;
  onSelectSkill: (skill: SkillSearchItem) => void;
  onUseCustomText: (customText: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

export function TaxonomySkillAutocomplete({
  initialValue = "",
  onSelectSkill,
  onUseCustomText,
  placeholder = "Search O*NET skills or enter custom requirement...",
  autoFocus = false,
}: TaxonomySkillAutocompleteProps) {
  const [query, setQuery] = useState(initialValue);
  const [results, setResults] = useState<SkillSearchItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRequestIdRef = useRef<number>(0);

  useEffect(() => {
    setQuery(initialValue);
  }, [initialValue]);

  // Handle outside click to close dropdown
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const executeSearch = async (searchTerm: string) => {
    const trimmed = searchTerm.trim();
    if (!trimmed) {
      setResults([]);
      setIsLoading(false);
      setIsOpen(false);
      setHasSearched(false);
      setSearchError(null);
      return;
    }

    const requestId = ++activeRequestIdRef.current;
    setIsLoading(true);
    setSearchError(null);

    try {
      const data = await searchSkills(trimmed, 8);
      if (requestId === activeRequestIdRef.current) {
        setResults(data || []);
        setHasSearched(true);
        setIsOpen(true);
        setSelectedIndex(-1);
      }
    } catch (err: any) {
      if (requestId === activeRequestIdRef.current) {
        setSearchError("Taxonomy service is temporarily unavailable. Custom entry remains fully supported.");
        setResults([]);
        setHasSearched(true);
        setIsOpen(true);
      }
    } finally {
      if (requestId === activeRequestIdRef.current) {
        setIsLoading(false);
      }
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVal = e.target.value;
    setQuery(newVal);
    setSearchError(null);

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    if (!newVal.trim()) {
      setResults([]);
      setIsOpen(false);
      setHasSearched(false);
      return;
    }

    debounceTimerRef.current = setTimeout(() => {
      executeSearch(newVal);
    }, 200);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen) {
      if (e.key === "ArrowDown" && query.trim()) {
        executeSearch(query);
      }
      return;
    }

    const totalOptions = results.length + (query.trim() ? 1 : 0);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (totalOptions > 0 ? (prev + 1) % totalOptions : -1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) =>
        totalOptions > 0 ? (prev <= 0 ? totalOptions - 1 : prev - 1) : -1
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < results.length) {
        handleSelectSuggestion(results[selectedIndex]);
      } else {
        handleSelectCustom();
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setIsOpen(false);
    }
  };

  const handleSelectSuggestion = (skill: SkillSearchItem) => {
    setQuery(skill.canonicalLabel);
    setIsOpen(false);
    onSelectSkill(skill);
  };

  const handleSelectCustom = () => {
    const trimmed = query.trim();
    if (trimmed) {
      setIsOpen(false);
      onUseCustomText(trimmed);
    }
  };

  const formatSourceFamily = (family?: string) => {
    if (!family) return "Software skill";
    const fam = family.toLowerCase();
    if (fam === "essential") return "Essential skill";
    if (fam === "transferable") return "Transferable skill";
    if (fam === "software") return "Software skill";
    return `${family} skill`;
  };

  return (
    <div className="taxonomy-autocomplete-container" ref={containerRef}>
      <div className="autocomplete-input-wrapper">
        <Icon name="search" size={15} className="autocomplete-search-icon" />
        <input
          type="text"
          className="form-input autocomplete-input"
          placeholder={placeholder}
          value={query}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (query.trim() && (results.length > 0 || searchError)) setIsOpen(true);
          }}
          autoFocus={autoFocus}
          aria-expanded={isOpen}
          aria-autocomplete="list"
          aria-label="Search skill or enter custom requirement"
        />
        {isLoading && <div className="loading-spinner-sm autocomplete-spinner" />}
        {query && !isLoading && (
          <button
            type="button"
            className="autocomplete-clear-btn"
            onClick={() => {
              setQuery("");
              setResults([]);
              setIsOpen(false);
              setSearchError(null);
            }}
            aria-label="Clear skill search"
          >
            <Icon name="x" size={14} />
          </button>
        )}
      </div>

      {isOpen && (
        <div className="taxonomy-dropdown-card" role="listbox">
          {searchError && (
            <div className="dropdown-alert-banner" role="alert">
              <Icon name="alert-triangle" size={14} />
              <span>{searchError}</span>
            </div>
          )}

          {!searchError && results.length > 0 && (
            <div className="dropdown-section">
              <div className="dropdown-section-title">O*NET 31.0 Skills</div>
              {results.map((item, idx) => (
                <div
                  key={item.conceptId}
                  className={`dropdown-item ${selectedIndex === idx ? "item-selected" : ""}`}
                  role="option"
                  aria-selected={selectedIndex === idx}
                  onClick={() => handleSelectSuggestion(item)}
                >
                  <div className="item-main-line">
                    <span className="item-canonical-label">{item.canonicalLabel}</span>
                    {item.matchedAlias && (
                      <span className="item-matched-alias">Alias: &quot;{item.matchedAlias}&quot;</span>
                    )}
                  </div>
                  <div className="item-meta-line">
                    <span className="item-category-pill">{formatSourceFamily(item.sourceFamily)}</span>
                    {item.category && item.category !== item.sourceFamily && (
                      <span className="item-source-tag">{item.category}</span>
                    )}
                    <span className="item-source-tag">O*NET 31.0</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {!searchError && hasSearched && results.length === 0 && (
            <div className="dropdown-empty-state">
              <span>
                No O*NET skill matches this text. Occupation titles belong in the O*NET occupation reference field. You may still add a custom requirement.
              </span>
            </div>
          )}

          {query.trim() && (
            <div className="dropdown-custom-action-wrapper">
              <button
                type="button"
                className={`dropdown-custom-btn ${
                  selectedIndex === results.length ? "item-selected" : ""
                }`}
                onClick={handleSelectCustom}
              >
                <Icon name="plus" size={14} />
                <span>Use custom requirement: &quot;<strong>{query.trim()}</strong>&quot;</span>
              </button>
            </div>
          )}

          <div className="dropdown-attribution-footer" style={{ padding: "6px 10px", fontSize: "10px", color: "var(--text-muted)", borderTop: "1px solid var(--border-subtle)", background: "var(--surface-sunken)" }}>
            Includes information from the O*NET 31.0 Database by USDOL/ETA, used under CC BY 4.0. O*NET is a trademark of USDOL/ETA.
          </div>
        </div>
      )}
    </div>
  );
}

