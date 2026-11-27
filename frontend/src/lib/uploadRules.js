/** Which files the Library and the chat box accept. Framework-free. */
export const ACCEPTED = ['.pdf', '.docx', '.txt', '.md'];
export const MAX_MB = 20;

/** The reason a file can't be added, or null when it can. */
export function checkFile(file) {
  const dot = file.name.lastIndexOf('.');
  const ext = dot < 0 ? '' : file.name.slice(dot).toLowerCase();
  if (!ACCEPTED.includes(ext)) return `${file.name}: only PDF, DOCX, TXT and MD files can be added.`;
  if (file.size > MAX_MB * 1024 * 1024) return `${file.name}: larger than ${MAX_MB} MB.`;
  return null;
}
