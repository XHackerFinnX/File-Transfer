export const PARAMS = [
  { key: 'netHeight', label: 'Высота сетки', min: 100, max: 460, step: .5, value: 322.5, unit: 'ед.', group: 'court' },
  { key: 'netWidth', label: 'Толщина стойки', min: 4, max: 24, step: 1, value: 8, unit: 'ед.', group: 'court' },
  { key: 'courtWidth', label: 'Ширина площадки', min: 900, max: 1600, step: 50, value: 1100, unit: 'ед.', group: 'court' },
  { key: 'serveHeight', label: 'Высота подачи', min: 180, max: 420, step: 5, value: 280, unit: 'ед.', group: 'court', help: 'Мяч появляется не выше, чем подающий способен достать прыжком.' },
  { key: 'speed1', label: 'Скорость игрока 1', min: 25, max: 250, step: 5, value: 100, unit: '%', group: 'players' },
  { key: 'speed2', label: 'Скорость игрока 2 / бота', min: 25, max: 250, step: 5, value: 100, unit: '%', group: 'players' },
  { key: 'jump1', label: 'Сила прыжка игрока 1', min: 50, max: 180, step: 5, value: 100, unit: '%', group: 'players' },
  { key: 'jump2', label: 'Сила прыжка игрока 2 / бота', min: 50, max: 180, step: 5, value: 100, unit: '%', group: 'players' },
  { key: 'acceleration', label: 'Разгон игроков', min: 50, max: 200, step: 5, value: 100, unit: '%', group: 'players' },
  { key: 'ballSpeed', label: 'Скорость мяча', min: 50, max: 200, step: 5, value: 100, unit: '%', group: 'ball' },
  { key: 'ballSize', label: 'Размер мяча', min: 50, max: 150, step: 5, value: 100, unit: '%', group: 'ball' },
  { key: 'hitPower', label: 'Сила удара', min: 50, max: 200, step: 5, value: 100, unit: '%', group: 'ball' },
  { key: 'gravity', label: 'Гравитация', min: 30, max: 180, step: 5, value: 100, unit: '%', group: 'ball' },
  { key: 'airDrag', label: 'Сопротивление воздуха', min: 0, max: 3, step: .1, value: 1, unit: '%', group: 'ball' },
  { key: 'tempo', label: 'Общий темп игры', min: 25, max: 200, step: 5, value: 100, unit: '%', group: 'rules', help: 'Меняет скорость всей симуляции. Скорости игроков и мяча настраиваются отдельно.' },
  { key: 'winScore', label: 'Очков для победы', min: 1, max: 50, step: 1, value: 5, unit: '', group: 'rules' }
];
export const DEFAULT_SETTINGS = Object.freeze(Object.assign(Object.fromEntries(PARAMS.map(p => [p.key, p.value])), {
  maxTouches: 3, winByTwo: false, hellUnlimited: true, particles: true, trail: true, shake: true
}));

export function normalizeSettings(raw) {
  const clean = { ...DEFAULT_SETTINGS };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return clean;
  for (const spec of PARAMS) {
    if (typeof raw[spec.key] !== 'number' || !Number.isFinite(raw[spec.key])) continue;
    const value = Math.max(spec.min, Math.min(spec.max, raw[spec.key]));
    clean[spec.key] = Number((Math.round((value - spec.min) / spec.step) * spec.step + spec.min).toFixed(3));
  }
  if (Number.isInteger(raw.maxTouches) && raw.maxTouches >= 0 && raw.maxTouches <= 10) clean.maxTouches = raw.maxTouches;
  for (const key of ['winByTwo','hellUnlimited','particles','trail','shake']) {
    if (typeof raw[key] === 'boolean') clean[key] = raw[key];
  }
  return clean;
}

export const COLORS = Object.freeze(['#000000','#ff0000','#0000ff','#00cc00','#ff00ff']);
export function validateSettings(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('INVALID_SETTINGS');
  const allowed = new Set(Object.keys(DEFAULT_SETTINGS));
  for (const key of Object.keys(raw)) if (!allowed.has(key)) throw new Error('INVALID_SETTINGS');
  for (const p of PARAMS) {
    if (!(p.key in raw)) continue;
    const value=raw[p.key];
    if (typeof value!=='number' || !Number.isFinite(value) || value<p.min || value>p.max) throw new Error('INVALID_SETTINGS');
    const steps=(value-p.min)/p.step;
    if (Math.abs(steps-Math.round(steps))>1e-6) throw new Error('INVALID_SETTINGS');
  }
  for (const key of ['winByTwo','hellUnlimited','particles','trail','shake']) if (key in raw && typeof raw[key]!=='boolean') throw new Error('INVALID_SETTINGS');
  if ('maxTouches' in raw && (!Number.isInteger(raw.maxTouches) || raw.maxTouches<0 || raw.maxTouches>10)) throw new Error('INVALID_SETTINGS');
  return normalizeSettings(raw);
}
