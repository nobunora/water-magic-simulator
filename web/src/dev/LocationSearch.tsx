import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  searchLocation,
  type GeocodeCandidateResponse,
  type GeocodeResponse,
} from "../api/client";

type Props = {
  disabled: boolean;
  onSelect: (lon: number, lat: number) => void;
  allowManualCoordinates?: boolean;
};

export default function LocationSearch({ disabled, onSelect, allowManualCoordinates = true }: Props) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<GeocodeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => requestRef.current?.abort();
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = query.trim();
    setError(null);

    if (!trimmed) {
      setResult(null);
      setError("検索語を入力してください。");
      return;
    }
    if ([...trimmed].length > 200) {
      setResult(null);
      setError("検索語は200文字以内で入力してください。");
      return;
    }

    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setResult(null);
    setLoading(true);

    try {
      const next = await searchLocation(trimmed, controller.signal);
      setResult(next);
      if (next.candidates.length === 0) {
        setError(allowManualCoordinates ? "候補が見つかりませんでした。緯度経度を直接入力できます。" : "候補が見つかりませんでした。住所・地名を変えて再検索してください。");
      }
    } catch (cause: unknown) {
      if (controller.signal.aborted) return;
      setResult(null);
      setError(allowManualCoordinates ? "住所・地名検索を利用できません。緯度経度を直接入力できます。" : "住所・地名検索を利用できません。時間をおいて再検索してください。");
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  };

  const handleSelect = (candidate: GeocodeCandidateResponse) => {
    setQuery(candidate.title);
    setResult((current) =>
      current ? { ...current, candidates: [] } : current,
    );
    setError(null);
    onSelect(candidate.lon, candidate.lat);
  };

  return (
    <div className="location-search">
      <form onSubmit={(event) => void handleSubmit(event)}>
        <label htmlFor="location-search-input">住所・地名を検索</label>
        <div className="location-search-row">
          <input
            id="location-search-input"
            value={query}
            disabled={disabled}
            placeholder="例: 新宿駅 / 東京都千代田区千代田1-1"
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" disabled={disabled || loading}>
            {loading ? (
              <>
                <span className="location-search-spinner" aria-hidden="true" />
                検索中…
              </>
            ) : (
              "検索"
            )}
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {error && <p className="location-search-error">{error}</p>}
        {result && result.candidates.length > 0 && (
          <ul className="location-search-candidates" aria-label="住所・地名検索候補">
            {result.candidates.map((candidate, index) => (
              <li key={`${candidate.provider}-${candidate.lat}-${candidate.lon}-${index}`}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => handleSelect(candidate)}
                >
                  <strong>{candidate.title}</strong>
                  {candidate.converted && candidate.converted !== candidate.title && (
                    <span>{candidate.converted}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {result && (
        <p className="location-search-attribution">
          住所・地名検索:{" "}
          <a href={result.attribution.url} target="_blank" rel="noreferrer">
            {result.attribution.text}
          </a>
        </p>
      )}
    </div>
  );
}
