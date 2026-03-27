import React, { useState } from 'react';
import { X, Upload, AlertCircle, CheckCircle2 } from 'lucide-react';
import { api } from '../../lib/api';
import toast from 'react-hot-toast';

interface Props {
  entityType: string;
  onImported: () => void;
  onClose: () => void;
}

interface ParseResult {
  headers: string[];
  rows: Record<string, string>[];
  rawCount: number;
}

// ─── CSV parser (no external library) ────────────────────────────────────────

function parseCsv(text: string): ParseResult {
  const lines = text.split('\n').map((l) => l.trimEnd()).filter((l) => l.length > 0);
  if (lines.length < 2) return { headers: [], rows: [], rawCount: 0 };

  function splitLine(line: string): string[] {
    const fields: string[] = [];
    let i = 0;
    while (i < line.length) {
      if (line[i] === '"') {
        // quoted field
        let field = '';
        i++; // skip opening quote
        while (i < line.length) {
          if (line[i] === '"' && line[i + 1] === '"') {
            field += '"';
            i += 2;
          } else if (line[i] === '"') {
            i++; // skip closing quote
            break;
          } else {
            field += line[i];
            i++;
          }
        }
        // skip optional comma
        if (line[i] === ',') i++;
        fields.push(field);
      } else {
        // unquoted field
        const end = line.indexOf(',', i);
        if (end === -1) {
          fields.push(line.slice(i));
          break;
        } else {
          fields.push(line.slice(i, end));
          i = end + 1;
        }
      }
    }
    return fields;
  }

  const headers = splitLine(lines[0]).map((h) => h.trim());
  const rows: Record<string, string>[] = [];

  for (let r = 1; r < lines.length; r++) {
    const values = splitLine(lines[r]);
    const row: Record<string, string> = {};
    headers.forEach((h, idx) => {
      row[h] = (values[idx] ?? '').trim();
    });
    rows.push(row);
  }

  return { headers, rows, rawCount: lines.length - 1 };
}

// ─── Modal ────────────────────────────────────────────────────────────────────

export function CsvImportModal({ entityType, onImported, onClose }: Props) {
  const [csvText, setCsvText] = useState('');
  const [parsed, setParsed] = useState<ParseResult | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{
    imported: number;
    total_rows: number;
    errors: object[];
  } | null>(null);

  function handleParse() {
    setParseError(null);
    setParsed(null);
    setImportResult(null);

    if (!csvText.trim()) {
      setParseError('Please paste some CSV data first.');
      return;
    }

    const result = parseCsv(csvText);
    if (result.headers.length === 0) {
      setParseError('Could not find any headers. Make sure the first row contains column names.');
      return;
    }
    if (result.rows.length === 0) {
      setParseError('No data rows found after the header row.');
      return;
    }

    setParsed(result);
  }

  async function handleImport() {
    if (!parsed) return;
    setImporting(true);
    setImportResult(null);
    try {
      const result = await api.compliance.importRows({
        entity_type: entityType,
        rows: parsed.rows,
      });
      setImportResult(result);
      if (result.imported > 0) {
        toast.success(`Imported ${result.imported} of ${result.total_rows} rows`);
        onImported();
      } else {
        toast.error('No rows were imported. Check the errors below.');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Import failed';
      toast.error(msg);
    } finally {
      setImporting(false);
    }
  }

  const entityLabel = entityType.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-2xl bg-white rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Upload size={18} className="text-slate-500" />
            <h2 className="text-base font-bold text-slate-900">Import {entityLabel} from CSV</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-300 hover:text-slate-600 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {/* CSV input */}
          {!importResult && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">
                Paste CSV data — first row must be column headers
              </label>
              <textarea
                className="w-full h-40 px-3 py-2.5 border border-slate-200 rounded-lg text-sm font-mono text-slate-700 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent placeholder:text-slate-300"
                placeholder={`state_code,license_type,license_number,expiration_date\nCA,Importer License,LIC-001,2025-12-31\nNY,Wholesale License,LIC-002,2025-06-30`}
                value={csvText}
                onChange={(e) => {
                  setCsvText(e.target.value);
                  setParsed(null);
                  setParseError(null);
                }}
              />
              {parseError && (
                <div className="mt-2 flex items-start gap-1.5 text-sm text-red-600">
                  <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
                  {parseError}
                </div>
              )}
            </div>
          )}

          {/* Preview table */}
          {parsed && !importResult && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium text-slate-700">
                  Preview — first {Math.min(5, parsed.rows.length)} of {parsed.rawCount} rows
                </p>
                <span className="text-xs text-slate-400">{parsed.headers.length} columns detected</span>
              </div>
              <div className="overflow-x-auto border border-slate-200 rounded-lg">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      {parsed.headers.map((h) => (
                        <th key={h} className="text-left px-3 py-2 font-semibold text-slate-600 whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {parsed.rows.slice(0, 5).map((row, i) => (
                      <tr key={i} className="hover:bg-slate-50">
                        {parsed.headers.map((h) => (
                          <td key={h} className="px-3 py-2 text-slate-600 whitespace-nowrap max-w-[160px] truncate">
                            {row[h] || <span className="text-slate-300">—</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Import result */}
          {importResult && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 p-4 bg-emerald-50 border border-emerald-200 rounded-xl">
                <CheckCircle2 size={20} className="text-emerald-600 flex-shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-emerald-800">
                    Imported {importResult.imported} of {importResult.total_rows} rows
                  </p>
                  {importResult.errors.length === 0 && (
                    <p className="text-xs text-emerald-600 mt-0.5">All rows processed successfully.</p>
                  )}
                </div>
              </div>

              {importResult.errors.length > 0 && (
                <div>
                  <p className="text-sm font-semibold text-red-700 mb-2">
                    {importResult.errors.length} row{importResult.errors.length !== 1 ? 's' : ''} had errors:
                  </p>
                  <div className="bg-red-50 border border-red-200 rounded-lg p-3 max-h-40 overflow-y-auto">
                    {importResult.errors.map((err, i) => (
                      <p key={i} className="text-xs text-red-700 font-mono mb-1">
                        {JSON.stringify(err)}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-100 flex-shrink-0 bg-slate-50 rounded-b-2xl">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800 transition-colors"
          >
            {importResult ? 'Close' : 'Cancel'}
          </button>

          <div className="flex items-center gap-3">
            {!importResult && (
              <>
                {!parsed ? (
                  <button
                    onClick={handleParse}
                    className="px-5 py-2 bg-slate-800 hover:bg-slate-900 text-white text-sm font-semibold rounded-lg transition-colors"
                  >
                    Parse
                  </button>
                ) : (
                  <>
                    <button
                      onClick={() => { setParsed(null); setParseError(null); }}
                      className="px-4 py-2 text-sm font-medium text-slate-500 hover:text-slate-700 transition-colors"
                    >
                      Edit CSV
                    </button>
                    <button
                      onClick={handleImport}
                      disabled={importing}
                      className="flex items-center gap-1.5 px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm font-semibold rounded-lg transition-colors"
                    >
                      {importing ? (
                        <>
                          <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                          Importing…
                        </>
                      ) : (
                        <>
                          <Upload size={14} />
                          Import {parsed.rawCount} row{parsed.rawCount !== 1 ? 's' : ''}
                        </>
                      )}
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
