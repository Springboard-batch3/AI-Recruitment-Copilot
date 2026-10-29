import React, { useState, useEffect, useRef } from "react";
import { searchOccupations, OccupationSearchItem } from "../../api/taxonomy";
import { Icon } from "../Icon";

interface OccupationSelectorProps {
  selectedConceptId?: string | null;
  selectedTitle?: string | null;
  onSelectOccupation: (item: OccupationSearchItem | null) => void;
}

export function OccupationSelector({
  selectedConceptId,
  selectedTitle,
  onSelectOccupation,
}: OccupationSelectorProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<OccupationSearchItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRequestIdRef = useRef<number>(0);

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
      const data = await searchOccupations(trimmed, 8);
      // Ensure only the latest request updates state
      if (requestId === activeRequestIdRef.current) {
        setResults(data || []);
        setHasSearched(true);
        setIsOpen(true);
        setSelectedIndex(-1);
      }
    } catch (err: any) {
      if (requestId === activeRequestIdRef.current) {
        setSearchError("Taxonomy service is temporarily unavailable. Selection is optional and draft creation can proceed.");
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
    const val = e.target.value;
    setQuery(val);
    setSearchError(null);

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    if (!val.trim()) {
      setResults([]);
      setIsOpen(false);
      setHasSearched(false);
      return;
    }

    debounceTimerRef.current = setTimeout(() => {
      executeSearch(val);
    }, 200);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen) {
      if (e.key === "ArrowDown" && query.trim()) {
        executeSearch(query);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (results.length > 0 ? (prev + 1) % results.length : -1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) =>
        results.length > 0 ? (prev <= 0 ? results.length - 1 : prev - 1) : -1
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < results.length) {
        handleSelect(results[selectedIndex]);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setIsOpen(false);
    }
  };

  const handleSelect = (item: OccupationSearchItem) => {
    onSelectOccupation(item);
    setQuery("");
    setResults([]);
    setIsOpen(false);
  };

  const handleClear = () => {
    onSelectOccupation(null);
    setQuery("");
    setResults([]);
    setSearchError(null);
  };

  if (selectedConceptId && selectedTitle) {
    return (
      <div className="selected-occupation-badge-card">
        <div className="occupation-badge-info">
          <Icon name="briefcase" size={16} className="occupation-icon" />
          <div>
            <span className="occupation-badge-title">{selectedTitle}</span>
            <span className="occupation-badge-sub">O*NET 31.0 Advisory Occupation Reference</span>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm clear-occupation-btn"
          onClick={handleClear}
          aria-label="Remove associated occupation"
        >
          <Icon name="x" size={13} />
          <span>Remove / Change</span>
        </button>
      </div>
    );
  }

  return (
    <div className="taxonomy-autocomplete-container" ref={containerRef}>
      <div className="autocomplete-input-wrapper">
        <Icon name="search" size={15} className="autocomplete-search-icon" />
        <input
          type="text"
          className="form-input autocomplete-input"
          placeholder="Search O*NET occupation reference (e.g. Software Developers, Data Scientists)..."
          value={query}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          onFocus={() => {
            if (query.trim() && (results.length > 0 || searchError)) setIsOpen(true);
          }}
          aria-label="Search O*NET occupation reference"
          aria-expanded={isOpen}
          aria-autocomplete="list"
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
            aria-label="Clear occupation search"
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
              <div className="dropdown-section-title">O*NET 31.0 Occupations</div>
              {results.map((item, idx) => (
                <div
                  key={item.conceptId}
                  className={`dropdown-item ${selectedIndex === idx ? "item-selected" : ""}`}
                  role="option"
                  aria-selected={selectedIndex === idx}
                  onClick={() => handleSelect(item)}
                >
                  <div className="item-main-line">
                    <span className="item-canonical-label">{item.canonicalTitle}</span>
                    <span className="item-soc-code">{item.onetSocCode}</span>
                  </div>
                  {item.matchedAlias && (
                    <div className="item-meta-line">
                      <span className="item-matched-alias">Matched Title: {item.matchedAlias}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {!searchError && hasSearched && results.length === 0 && (
            <div className="dropdown-empty-state">
              <span>
                No standard occupation found for &quot;{query}&quot;. Try a standard O*NET title such as &quot;Software Developers&quot;, or continue without selecting one.
              </span>
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
