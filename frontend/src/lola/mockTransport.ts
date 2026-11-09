/**
 * Scripted demo transport — used for local demos, component tests and the
 * end-to-end acceptance suite. It follows the SAME contract and the SAME
 * honesty rules as the real backend: it streams with realistic delays, emits
 * `working` steps, refuses forbidden requests, reports connector outages and
 * only returns verified receipts when it "confirmed" the effect.
 *
 * Every briefing it returns is flagged `demo: true` so the UI shows a badge.
 */
import type { Role } from '../hooks/useAuth';
import type {
  ActionPreview, Briefing, EvidenceCard, LolaEvent, LolaLanguage, Receipt, TurnRequest,
} from './contract';
import type { LolaTransport } from './transport';
import { canApproveActions, canSeeFinancials, filterBriefingForRole } from './permissions';
import { greeting } from './persona';
import { currentRole } from '../hooks/useAuth';

export interface MockOptions {
  /** Milliseconds per streamed word. 0 for instant tests. */
  wordDelayMs?: number;
  /** Milliseconds spent in each `working` step. */
  stepDelayMs?: number;
  /** Simulate these connectors being down. */
  outages?: string[];
  /** Override role resolution (tests). */
  role?: () => Role;
}

const AS_OF = () => new Date(Date.now() - 12 * 60_000).toISOString();

type Bi = { en: string; es: string };
const L = (lang: LolaLanguage, b: Bi) => b[lang];

interface Script {
  steps: Bi[];
  text: Bi;
  cards?: (lang: LolaLanguage) => EvidenceCard[];
  action?: (lang: LolaLanguage, role: Role) => ActionPreview;
  suggestions?: Bi[];
  /** Requires financial visibility. */
  financial?: boolean;
  connector?: string;
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
  });
}

function approvalFor(role: Role, a: Omit<ActionPreview, 'can_approve' | 'approval_note' | 'status'>, lang: LolaLanguage): ActionPreview {
  const ok = canApproveActions(role);
  return {
    ...a,
    status: 'proposed',
    can_approve: ok,
    approval_note: ok ? undefined : L(lang, { en: 'You can’t approve this. I’ve queued it for a manager.', es: 'No puedes aprobar esto. Lo dejé en cola para un gerente.' }),
  };
}

const SCRIPTS: Record<string, Script> = {
  briefing: {
    financial: true,
    connector: 'QuickBooks Online',
    steps: [
      { en: 'Reading yesterday’s sales and AR', es: 'Revisando ventas y cuentas por cobrar de ayer' },
      { en: 'Checking shipments and warehouse', es: 'Revisando envíos y almacén' },
    ],
    text: {
      en: 'Overall a solid week. Revenue is up 6% on last week, driven by Sunrise Distributors and Delta Foods. Three things need you: $48.2k in invoices is past 30 days, container MSKU 448812 is on customs hold in Long Beach, and Delta Foods’ reorder is waiting on your approval. Want me to start with collections?',
      es: 'En general, una buena semana. Los ingresos suben 6% frente a la semana pasada, impulsados por Sunrise Distributors y Delta Foods. Tres cosas te necesitan: $48.2k en facturas con más de 30 días, el contenedor MSKU 448812 está retenido en aduana en Long Beach, y el pedido de Delta Foods espera tu aprobación. ¿Empiezo por cobranza?',
    },
    cards: (lang) => [
      {
        id: 'brief-metrics', kind: 'metrics', availability: 'ok', sensitivity: 'financial',
        title: L(lang, { en: 'This week at a glance', es: 'La semana de un vistazo' }),
        metrics: [
          { label: L(lang, { en: 'Revenue (7d)', es: 'Ingresos (7d)' }), value: '$312,400', delta: '+6.1%', tone: 'good' },
          { label: L(lang, { en: 'Gross margin', es: 'Margen bruto' }), value: '31.4%', delta: '−0.8 pt', tone: 'bad' },
          { label: L(lang, { en: 'AR > 30 days', es: 'CxC > 30 días' }), value: '$48,200', delta: '+$9.1k', tone: 'bad' },
          { label: L(lang, { en: 'Open orders', es: 'Pedidos abiertos' }), value: '27', tone: 'neutral' },
        ],
        evidence: [{ source: 'QuickBooks Online', as_of: AS_OF(), href: '/' }],
      },
      {
        id: 'brief-chart', kind: 'chart', chart: 'line', availability: 'ok', sensitivity: 'financial',
        title: L(lang, { en: 'Daily revenue, last 14 days', es: 'Ingresos diarios, últimos 14 días' }),
        x_key: 'day', series: [{ key: 'revenue', label: L(lang, { en: 'Revenue', es: 'Ingresos' }) }], value_format: 'currency',
        data: [38, 41, 36, 44, 40, 29, 22, 42, 47, 45, 49, 51, 33, 26].map((v, i) => ({ day: `D${i + 1}`, revenue: v * 1000 })),
        evidence: [{ source: 'QuickBooks Online', as_of: AS_OF() }],
      },
    ],
    suggestions: [
      { en: 'Show me the overdue invoices', es: 'Muéstrame las facturas vencidas' },
      { en: 'Where is container MSKU 448812?', es: '¿Dónde está el contenedor MSKU 448812?' },
      { en: 'Why is margin down?', es: '¿Por qué bajó el margen?' },
    ],
  },
  ar: {
    financial: true,
    connector: 'QuickBooks Online',
    steps: [{ en: 'Pulling open invoices from QuickBooks', es: 'Consultando facturas abiertas en QuickBooks' }],
    text: {
      en: 'Five invoices are past due, totaling $48,200. Harbor Wines is the biggest at $21,750 and 47 days late — they usually pay after one reminder. I’ve drafted reminders for the three oldest.',
      es: 'Hay cinco facturas vencidas por $48,200 en total. Harbor Wines es la mayor: $21,750 con 47 días de atraso — normalmente pagan después de un recordatorio. Preparé recordatorios para las tres más antiguas.',
    },
    cards: (lang) => [{
      id: 'ar-table', kind: 'table', availability: 'ok', sensitivity: 'financial',
      title: L(lang, { en: 'Overdue invoices', es: 'Facturas vencidas' }),
      columns: [
        { key: 'invoice', label: L(lang, { en: 'Invoice', es: 'Factura' }) },
        { key: 'customer', label: L(lang, { en: 'Customer', es: 'Cliente' }) },
        { key: 'days', label: L(lang, { en: 'Days late', es: 'Días de atraso' }), align: 'right' },
        { key: 'amount', label: L(lang, { en: 'Amount', es: 'Monto' }), align: 'right' },
      ],
      rows: [
        { invoice: 'INV-10442', customer: 'Harbor Wines', days: 47, amount: '$21,750' },
        { invoice: 'INV-10457', customer: 'Coastal Spirits', days: 39, amount: '$11,300' },
        { invoice: 'INV-10471', customer: 'Delta Foods', days: 33, amount: '$7,980' },
        { invoice: 'INV-10488', customer: 'Mesa Imports', days: 18, amount: '$4,420' },
        { invoice: 'INV-10490', customer: 'Sunrise Distributors', days: 9, amount: '$2,750' },
      ],
      total_label: L(lang, { en: 'Total overdue: $48,200', es: 'Total vencido: $48,200' }),
      evidence: [{ source: 'QuickBooks Online · A/R Aging', as_of: AS_OF(), href: '/accounts' }],
    }],
    action: (lang, role) => approvalFor(role, {
      id: 'act-ar-reminders', action_type: 'send_collection_email', risk: 'low',
      title: L(lang, { en: 'Send 3 payment reminders', es: 'Enviar 3 recordatorios de pago' }),
      summary: L(lang, { en: 'Polite reminder with invoice PDF, sent from your Gmail.', es: 'Recordatorio cordial con la factura en PDF, desde tu Gmail.' }),
      fields: [
        { label: L(lang, { en: 'Recipients', es: 'Destinatarios' }), value: 'Harbor Wines, Coastal Spirits, Delta Foods' },
        { label: L(lang, { en: 'Total requested', es: 'Total solicitado' }), value: '$41,030' },
        { label: L(lang, { en: 'Channel', es: 'Canal' }), value: 'Gmail' },
      ],
    }, lang),
    suggestions: [{ en: 'Which customers pay late most often?', es: '¿Qué clientes pagan tarde con más frecuencia?' }],
  },
  sales: {
    financial: true,
    connector: 'QuickBooks Online',
    steps: [
      { en: 'Comparing this week to last week', es: 'Comparando esta semana con la anterior' },
      { en: 'Breaking down by account and product', es: 'Desglosando por cuenta y producto' },
    ],
    text: {
      en: 'Sales fell 14% last week, but it’s concentrated: two accounts explain most of it. Coastal Spirits skipped their usual Tuesday order, and Reserva 12 was out of stock for four days. Excluding those, sales were flat.',
      es: 'Las ventas cayeron 14% la semana pasada, pero está concentrado: dos cuentas explican casi todo. Coastal Spirits no hizo su pedido habitual del martes y Reserva 12 estuvo agotado cuatro días. Sin eso, las ventas estuvieron estables.',
    },
    cards: (lang) => [
      {
        id: 'sales-expl', kind: 'financial_explanation', availability: 'ok', sensitivity: 'financial',
        title: L(lang, { en: 'What moved sales', es: 'Qué movió las ventas' }),
        headline: L(lang, { en: '−$43.8k vs prior week (−14%)', es: '−$43.8k vs semana anterior (−14%)' }),
        drivers: [
          { label: L(lang, { en: 'Coastal Spirits — no weekly order', es: 'Coastal Spirits — sin pedido semanal' }), impact: '−$24.1k', direction: 'down' },
          { label: L(lang, { en: 'Reserva 12 stock-out (4 days)', es: 'Reserva 12 agotado (4 días)' }), impact: '−$17.6k', direction: 'down' },
          { label: L(lang, { en: 'Other accounts', es: 'Otras cuentas' }), impact: '−$2.1k', direction: 'down' },
        ],
        evidence: [{ source: 'QuickBooks Online · Sales by Customer', as_of: AS_OF() }],
      },
      {
        id: 'sales-bars', kind: 'chart', chart: 'bar', availability: 'ok', sensitivity: 'financial',
        title: L(lang, { en: 'Top accounts, week over week', es: 'Principales cuentas, semana a semana' }),
        x_key: 'account', value_format: 'currency',
        series: [{ key: 'prior', label: L(lang, { en: 'Prior week', es: 'Semana previa' }) }, { key: 'last', label: L(lang, { en: 'Last week', es: 'Última semana' }) }],
        data: [
          { account: 'Coastal', prior: 24100, last: 0 },
          { account: 'Sunrise', prior: 51200, last: 53900 },
          { account: 'Delta', prior: 38800, last: 37400 },
          { account: 'Harbor', prior: 29500, last: 28700 },
        ],
        evidence: [{ source: 'QuickBooks Online', as_of: AS_OF() }],
      },
    ],
    action: (lang, role) => approvalFor(role, {
      id: 'act-call-coastal', action_type: 'create_follow_up', risk: 'low',
      title: L(lang, { en: 'Assign a call to Coastal Spirits', es: 'Asignar una llamada a Coastal Spirits' }),
      summary: L(lang, { en: 'Creates a follow-up task for their rep, due tomorrow.', es: 'Crea una tarea de seguimiento para su representante, para mañana.' }),
      fields: [{ label: L(lang, { en: 'Owner', es: 'Responsable' }), value: 'Maria (Sales)' }, { label: L(lang, { en: 'Due', es: 'Vence' }), value: L(lang, { en: 'Tomorrow, 10:00', es: 'Mañana, 10:00' }) }],
    }, lang),
  },
  container: {
    connector: 'Carrier tracking',
    steps: [{ en: 'Checking carrier and customs status', es: 'Consultando naviera y estado aduanal' }],
    text: {
      en: 'Container MSKU 448812 arrived in Long Beach on Tuesday but is on a customs exam hold. The broker expects release Thursday, which pushes delivery to Sunrise Distributors to Monday. Free time ends Friday, so demurrage starts after that.',
      es: 'El contenedor MSKU 448812 llegó a Long Beach el martes pero está retenido por revisión aduanal. El agente espera liberarlo el jueves, así que la entrega a Sunrise Distributors pasa al lunes. El tiempo libre termina el viernes; después empieza la demora.',
    },
    cards: (lang) => [{
      id: 'msku-timeline', kind: 'shipment_timeline', availability: 'ok',
      title: L(lang, { en: 'Container MSKU 448812', es: 'Contenedor MSKU 448812' }),
      reference: 'MSKU 448812 · PO-2291', carrier: 'Maersk', eta: L(lang, { en: 'Delivery: Mon (was Fri)', es: 'Entrega: lun (antes vie)' }),
      steps: [
        { label: L(lang, { en: 'Departed Valencia', es: 'Salió de Valencia' }), at: '2026-10-14', status: 'done' },
        { label: L(lang, { en: 'Arrived Long Beach', es: 'Llegó a Long Beach' }), at: '2026-11-03', status: 'done' },
        { label: L(lang, { en: 'Customs exam hold', es: 'Retención por revisión aduanal' }), at: '2026-11-04', status: 'exception', detail: L(lang, { en: 'CBP exam — broker: Pacific Customs', es: 'Revisión CBP — agente: Pacific Customs' }) },
        { label: L(lang, { en: 'Release (expected)', es: 'Liberación (esperada)' }), at: '2026-11-12', status: 'pending' },
        { label: L(lang, { en: 'Delivered to warehouse', es: 'Entregado en almacén' }), status: 'pending' },
      ],
      exceptions: [L(lang, { en: 'Demurrage risk after Fri Nov 13 (~$175/day)', es: 'Riesgo de demora después del vie 13 nov (~$175/día)' })],
      evidence: [{ source: 'Maersk tracking', as_of: AS_OF(), record_ref: 'MSKU4488120' }, { source: 'Lion BI Logistics', href: '/logistics' }],
    }],
    action: (lang, role) => approvalFor(role, {
      id: 'act-notify-sunrise', action_type: 'send_customer_update', risk: 'low',
      title: L(lang, { en: 'Tell Sunrise about the new date', es: 'Avisar a Sunrise de la nueva fecha' }),
      summary: L(lang, { en: 'Short email to their buyer with the revised Monday delivery.', es: 'Correo breve a su comprador con la nueva entrega del lunes.' }),
      fields: [{ label: L(lang, { en: 'To', es: 'Para' }), value: 'purchasing@sunrise.example' }],
    }, lang),
  },
  warehouse: {
    connector: 'Warehouse receiving',
    steps: [{ en: 'Matching receipts to the purchase order', es: 'Cruzando la recepción con la orden de compra' }],
    text: {
      en: 'Today’s receipt for PO-2287 doesn’t match: 12 cases short on Reserva 12 and 3 cases damaged on Blanco. I’d open a vendor claim and hold the invoice until it’s resolved.',
      es: 'La recepción de hoy de la PO-2287 no cuadra: faltan 12 cajas de Reserva 12 y 3 cajas de Blanco llegaron dañadas. Abriría un reclamo al proveedor y retendría la factura hasta resolverlo.',
    },
    cards: (lang) => [{
      id: 'recv-table', kind: 'table', availability: 'ok',
      title: L(lang, { en: 'Receiving exceptions · PO-2287', es: 'Excepciones de recepción · PO-2287' }),
      columns: [
        { key: 'sku', label: 'SKU' }, { key: 'expected', label: L(lang, { en: 'Expected', es: 'Esperado' }), align: 'right' },
        { key: 'received', label: L(lang, { en: 'Received', es: 'Recibido' }), align: 'right' }, { key: 'issue', label: L(lang, { en: 'Issue', es: 'Problema' }) },
      ],
      rows: [
        { sku: 'RSV-12-750', expected: 120, received: 108, issue: L(lang, { en: 'Short 12', es: 'Faltan 12' }) },
        { sku: 'BLN-750', expected: 80, received: 80, issue: L(lang, { en: '3 damaged', es: '3 dañadas' }) },
      ],
      evidence: [{ source: 'Warehouse receiving log', as_of: AS_OF(), record_ref: 'RCV-5512' }],
    }],
    action: (lang, role) => ({
      ...approvalFor(role, {
        id: 'act-vendor-claim', action_type: 'open_vendor_claim', risk: 'medium',
        title: L(lang, { en: 'Open vendor claim with Bodegas Sol', es: 'Abrir reclamo con Bodegas Sol' }),
        summary: L(lang, { en: 'Claim for 12 short + 3 damaged cases, with photos from receiving.', es: 'Reclamo por 12 cajas faltantes + 3 dañadas, con fotos de recepción.' }),
        fields: [{ label: L(lang, { en: 'Claim value', es: 'Valor del reclamo' }), value: '$2,310' }],
      }, lang),
      // Back office may open claims under the receiving policy (WS1 decides in prod).
      ...(role === 'back_office' ? { can_approve: true, approval_note: undefined } : {}),
    }),
  },
  accounts: {
    connector: 'QuickBooks Online',
    steps: [{ en: 'Checking order rhythm for your accounts', es: 'Revisando el ritmo de pedidos de tus cuentas' }],
    text: {
      en: 'Two of your accounts are off rhythm. Coastal Spirits normally orders weekly and it’s been 9 days. Mesa Imports is down 30% this month. I’d call Coastal first.',
      es: 'Dos de tus cuentas salieron de su ritmo. Coastal Spirits suele pedir cada semana y ya van 9 días. Mesa Imports bajó 30% este mes. Yo llamaría primero a Coastal.',
    },
    cards: (lang) => [{
      id: 'acct-table', kind: 'table', availability: 'ok',
      title: L(lang, { en: 'Accounts to call', es: 'Cuentas para llamar' }),
      columns: [{ key: 'account', label: L(lang, { en: 'Account', es: 'Cuenta' }) }, { key: 'signal', label: L(lang, { en: 'Signal', es: 'Señal' }) }, { key: 'last', label: L(lang, { en: 'Last order', es: 'Último pedido' }), align: 'right' }],
      rows: [
        { account: 'Coastal Spirits', signal: L(lang, { en: 'Missed weekly order', es: 'No hizo su pedido semanal' }), last: L(lang, { en: '9 days ago', es: 'hace 9 días' }) },
        { account: 'Mesa Imports', signal: L(lang, { en: 'Volume −30% MTD', es: 'Volumen −30% en el mes' }), last: L(lang, { en: '4 days ago', es: 'hace 4 días' }) },
      ],
      evidence: [{ source: 'Lion BI account health', as_of: AS_OF(), href: '/accounts' }],
    }],
    action: (lang, role) => ({
      ...approvalFor(role, {
        id: 'act-checkin-coastal', action_type: 'draft_email', risk: 'low',
        title: L(lang, { en: 'Draft a check-in to Coastal Spirits', es: 'Redactar un saludo a Coastal Spirits' }),
        summary: L(lang, { en: 'Friendly note asking about this week’s order. You review before it sends.', es: 'Nota amable preguntando por el pedido de esta semana. Tú la revisas antes de enviarla.' }),
        fields: [{ label: L(lang, { en: 'To', es: 'Para' }), value: 'buyer@coastal.example' }],
      }, lang),
      // Reps may send their own customer emails under the default policy.
      ...(role === 'sales_rep' ? { can_approve: true, approval_note: undefined } : {}),
    }),
  },
  inventory: {
    connector: 'QuickBooks Desktop',
    steps: [{ en: 'Reading stock levels', es: 'Leyendo niveles de inventario' }],
    text: {
      en: 'Two items will run out within two weeks at the current pace: Reserva 12 (1.4 weeks) and Rosado (1.9 weeks). Everything else has at least a month.',
      es: 'Dos productos se agotarán en menos de dos semanas al ritmo actual: Reserva 12 (1.4 semanas) y Rosado (1.9 semanas). Todo lo demás tiene al menos un mes.',
    },
    cards: (lang) => [{
      id: 'inv-low', kind: 'table', availability: 'ok',
      title: L(lang, { en: 'Running low', es: 'Por agotarse' }),
      columns: [{ key: 'item', label: L(lang, { en: 'Item', es: 'Producto' }) }, { key: 'cases', label: L(lang, { en: 'Cases', es: 'Cajas' }), align: 'right' }, { key: 'weeks', label: L(lang, { en: 'Weeks left', es: 'Semanas' }), align: 'right' }],
      rows: [{ item: 'Reserva 12', cases: 42, weeks: 1.4 }, { item: 'Rosado', cases: 61, weeks: 1.9 }],
      evidence: [{ source: 'QuickBooks Desktop', as_of: AS_OF(), href: '/inventory' }],
    }],
  },
};

/** Phrases that request information a role may not see. */
const RESTRICTED = /\b(margin|margen|payroll|nómina|nomina|profit|utilidad|salary|salario|p&l|ebitda|cash ?flow|flujo de caja)\b/i;

export function classify(message: string): keyof typeof SCRIPTS | 'unknown' {
  const m = message.toLowerCase().replace(/[\u2018\u2019]/g, "'");
  if (/(what'?s|what is|qué está|que esta|how'?s|cómo va|como va).*(happening|pasando|business|negocio|going)|brief|resumen|morning|buenos días|attention|atención|atencion|approval|aprobaci/.test(m)) return 'briefing';
  if (/(my accounts|mis cuentas|need a call|llamada|at risk|en riesgo)/.test(m)) return 'accounts';
  if (/(overdue|past due|vencid|collect|cobran|receivable|por cobrar|invoice|factura)/.test(m)) return 'ar';
  if (/(sales|ventas|revenue|ingresos).*(drop|down|fell|bajar|bajaron|cayer|why|por qué)|why.*(sales|margin)|por qué.*(ventas|margen)/.test(m)) return 'sales';
  if (/(container|contenedor|msku|shipment|envío|envio|arrive|llega|delivery|entrega|order arrive)/.test(m)) return 'container';
  if (/(receiv|recepci|warehouse|almac|damaged|dañad|short)/.test(m)) return 'warehouse';
  if (/(running low|inventory|inventario|stock|agotando|agota)/.test(m)) return 'inventory';
  return 'unknown';
}

function readOutages(): string[] {
  try {
    const q = new URLSearchParams(window.location.search).get('lola_outage');
    const s = window.localStorage.getItem('lola_demo_outage');
    return (q ?? s ?? '').split(',').map((x) => x.trim()).filter(Boolean);
  } catch { return []; }
}

export class MockLolaTransport implements LolaTransport {
  readonly kind = 'mock' as const;
  private readonly opts: Required<Omit<MockOptions, 'role'>> & { role: () => Role };
  private readonly proposed = new Map<string, ActionPreview>();
  private convo = 0;

  constructor(opts: MockOptions = {}) {
    this.opts = {
      wordDelayMs: opts.wordDelayMs ?? 28,
      stepDelayMs: opts.stepDelayMs ?? 650,
      outages: opts.outages ?? readOutages(),
      role: opts.role ?? currentRole,
    };
  }

  async *turn(req: TurnRequest, signal: AbortSignal): AsyncGenerator<LolaEvent> {
    const lang = req.language;
    const role = this.opts.role();
    try {
      yield { type: 'conversation', conversation_id: req.conversation_id ?? `demo-${++this.convo}` };
      yield { type: 'status', state: 'thinking' };
      await sleep(Math.min(350, this.opts.stepDelayMs), signal);

      if (req.attachments?.length) {
        const a = req.attachments[0];
        yield { type: 'status', state: 'working', step: L(lang, { en: `Reading ${a.name}`, es: `Leyendo ${a.name}` }) };
        await sleep(this.opts.stepDelayMs, signal);
      }

      // Permission denial (server-side in production; mirrored here).
      if (RESTRICTED.test(req.message) && !canSeeFinancials(role)) {
        yield {
          type: 'notice',
          notice: { kind: 'permission_denied', message: L(lang, { en: 'That’s restricted to owners and managers, so I can’t share it. I can help with your accounts, orders and deliveries.', es: 'Eso está restringido a dueños y gerentes, así que no puedo compartirlo. Puedo ayudarte con tus cuentas, pedidos y entregas.' }) },
        };
        yield { type: 'done' };
        return;
      }

      const key = classify(req.message);
      const script = key === 'unknown' ? null : SCRIPTS[key];

      if (!script) {
        const generic = req.attachments?.length
          ? L(lang, { en: `I’ve read ${req.attachments[0].name}. What would you like to know about it?`, es: `Ya leí ${req.attachments[0].name}. ¿Qué quieres saber de él?` })
          : L(lang, { en: 'I can pull up sales, collections, shipments, inventory and approvals. What would you like to look at?', es: 'Puedo revisar ventas, cobranza, envíos, inventario y aprobaciones. ¿Qué quieres ver?' });
        yield* this.words(generic, signal);
        yield { type: 'suggestions', items: [L(lang, { en: 'What’s happening with my business?', es: '¿Qué está pasando con mi negocio?' })] };
        yield { type: 'done' };
        return;
      }

      if (script.financial && !canSeeFinancials(role)) {
        yield {
          type: 'notice',
          notice: { kind: 'permission_denied', message: L(lang, { en: 'Company-wide financials are restricted for your role. I can show your own accounts instead.', es: 'Las finanzas de la empresa están restringidas para tu rol. Puedo mostrarte tus propias cuentas.' }) },
        };
        yield { type: 'suggestions', items: [L(lang, { en: 'Which of my accounts need a call?', es: '¿Cuáles de mis cuentas necesitan una llamada?' })] };
        yield { type: 'done' };
        return;
      }

      for (const step of script.steps) {
        yield { type: 'status', state: 'working', step: L(lang, step) };
        await sleep(this.opts.stepDelayMs, signal);
      }

      if (script.connector && this.opts.outages.some((o) => script.connector!.toLowerCase().includes(o.toLowerCase()))) {
        yield {
          type: 'notice',
          notice: { kind: 'connector_outage', connector: script.connector, message: L(lang, { en: `${script.connector} isn’t responding, so I can’t give you current numbers. I won’t guess. I’ll retry automatically, or you can check Connectors.`, es: `${script.connector} no responde, así que no puedo darte cifras actuales. No voy a adivinar. Reintentaré automáticamente, o revisa Conectores.` }) },
        };
        for (const c of script.cards?.(lang) ?? []) {
          yield { type: 'card', card: blanked(c, L(lang, { en: `${script.connector} unavailable`, es: `${script.connector} no disponible` })) };
        }
        yield { type: 'done' };
        return;
      }

      yield* this.words(L(lang, script.text), signal);
      for (const c of script.cards?.(lang) ?? []) {
        await sleep(Math.min(120, this.opts.stepDelayMs), signal);
        yield { type: 'card', card: c };
      }
      if (script.action) {
        const a = script.action(lang, role);
        this.proposed.set(a.id, a);
        yield { type: 'action_preview', action: a };
      }
      if (script.suggestions) yield { type: 'suggestions', items: script.suggestions.map((s) => L(lang, s)) };
      yield { type: 'done' };
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      throw e;
    }
  }

  private async *words(text: string, signal: AbortSignal): AsyncGenerator<LolaEvent> {
    const parts = text.split(/(\s+)/);
    for (let i = 0; i < parts.length; i += 4) {
      if (this.opts.wordDelayMs) await sleep(this.opts.wordDelayMs * 2, signal);
      yield { type: 'text', delta: parts.slice(i, i + 4).join('') };
    }
  }

  async approve(actionId: string): Promise<Receipt> {
    const a = this.proposed.get(actionId);
    if (!a) throw new Error('That action is no longer available.');
    if (!a.can_approve) throw new Error('You don’t have permission to approve this.');
    await new Promise((r) => setTimeout(r, Math.min(900, this.opts.stepDelayMs + 250)));
    this.proposed.delete(actionId);
    if (a.action_type === 'open_vendor_claim') {
      // Demonstrates an honest "submitted, not yet confirmed" receipt.
      return { action_id: actionId, status: 'submitted', verified: false, message: 'Claim submitted to the vendor portal. They haven’t confirmed receipt yet — I’ll tell you when they do.', reference: 'CLM-0193' };
    }
    return { action_id: actionId, status: 'executed', verified: true, message: `${a.title} — done and confirmed.`, reference: `RCPT-${actionId.slice(-4).toUpperCase()}`, executed_at: new Date().toISOString() };
  }

  async reject(actionId: string): Promise<void> {
    this.proposed.delete(actionId);
  }

  async briefing(role: Role): Promise<Briefing> {
    await new Promise((r) => setTimeout(r, Math.min(400, this.opts.stepDelayMs)));
    const qbDown = this.opts.outages.some((o) => 'quickbooks online'.includes(o.toLowerCase()));
    const b: Briefing = {
      demo: true,
      generated_at: new Date().toISOString(),
      greeting: greeting('en'),
      headline: qbDown && canSeeFinancials(role)
        ? 'QuickBooks isn’t responding, so I’ve left out sales and cash figures. Operations look normal.'
        : role === 'sales_rep'
          ? 'Two of your accounts need a call today, and one delivery moved.'
          : role === 'back_office'
            ? 'One receiving exception and two inbound containers today.'
            : 'Revenue is up 6% this week. Collections and one customs hold need you.',
      sections: [
        { id: 'morning_briefing', title: 'Morning briefing', availability: qbDown ? 'partial' : 'ok', availability_note: qbDown ? 'QuickBooks figures left out until it reconnects.' : undefined, items: [
          ...(qbDown && role !== 'sales_rep' ? [] : [{ id: 'mb1', title: role === 'sales_rep' ? 'Coastal Spirits skipped their weekly order' : 'Revenue up 6.1% week over week', severity: (role === 'sales_rep' ? 'warning' : 'good') as 'warning' | 'good', ask: 'What’s happening with my business?' }]),
          { id: 'mb2', title: 'Container MSKU 448812 on customs hold', severity: 'warning', ask: 'Where is container MSKU 448812?' },
        ] },
        { id: 'critical_alerts', title: 'Critical alerts', availability: qbDown ? 'partial' : 'ok', availability_note: qbDown ? 'Receivables alerts paused: QuickBooks Online isn’t responding.' : undefined, sensitivity: 'executive', items: [
          ...(qbDown ? [] : [{ id: 'ca1', title: '$48.2k in invoices past 30 days', severity: 'critical' as const, ask: 'Which invoices are overdue?' }]),
          { id: 'ca2', title: 'Reserva 12 out of stock in 1.4 weeks', severity: 'critical', ask: 'What’s running low this week?', href: '/inventory' },
        ] },
        qbDown
          ? { id: 'performance', title: 'Business performance', availability: 'unavailable', availability_note: 'QuickBooks Online isn’t responding. Figures hidden rather than shown stale.', items: [], sensitivity: 'financial' }
          : { id: 'performance', title: 'Business performance', availability: 'ok', sensitivity: 'financial', items: [
              { id: 'p1', title: 'Revenue (7d)', meta: '$312.4k · +6.1%', severity: 'good' },
              { id: 'p2', title: 'Gross margin', meta: '31.4% · −0.8 pt', severity: 'warning', ask: 'Why is margin down?' },
              { id: 'p3', title: 'Cash collected (7d)', meta: '$268.9k', severity: 'info' },
            ], evidence: [{ source: 'QuickBooks Online', as_of: AS_OF() }] },
        { id: 'department_priorities', title: 'Department priorities', availability: 'ok', items:
          role === 'sales_rep' ? [
            { id: 'dp-s1', title: 'Call Coastal Spirits', detail: 'No order in 9 days (usually weekly)', severity: 'warning', ask: 'Which of my accounts need a call?' },
            { id: 'dp-s2', title: 'Sunrise delivery moved to Monday', detail: 'Customs hold on MSKU 448812', severity: 'info', ask: 'When will Sunrise Distributors’ order arrive?' },
          ] : role === 'back_office' ? [
            { id: 'dp-b1', title: 'PO-2287 receiving mismatch', detail: '12 short · 3 damaged', severity: 'critical', ask: 'Any receiving exceptions today?' },
            { id: 'dp-b2', title: '2 containers arriving this week', severity: 'info', ask: 'What’s arriving at the warehouse this week?' },
          ] : [
            { id: 'dp1', title: 'Sales: Coastal Spirits order missing', severity: 'warning', ask: 'Why did sales drop last week?' },
            { id: 'dp2', title: 'Warehouse: PO-2287 mismatch', severity: 'warning', ask: 'Any receiving exceptions today?' },
            { id: 'dp3', title: 'Compliance: TX license renews in 21 days', severity: 'info', href: '/compliance' },
          ] },
        { id: 'logistics_exceptions', title: 'Logistics exceptions', availability: 'ok', items: [
          { id: 'le1', title: 'MSKU 448812 — CBP exam hold', detail: 'Release expected Thu; demurrage after Fri', severity: 'warning', ask: 'Where is container MSKU 448812?' },
        ] },
        { id: 'financial_risks', title: 'Financial risks', availability: qbDown ? 'unavailable' : 'ok', availability_note: qbDown ? 'QuickBooks Online isn’t responding.' : undefined, sensitivity: 'financial', items: qbDown ? [] : [
          { id: 'fr1', title: 'Harbor Wines — $21.8k, 47 days late', severity: 'critical', ask: 'Which invoices are overdue?' },
          { id: 'fr2', title: 'Margin compression on Blanco (−2.1 pt)', severity: 'warning', ask: 'Why is margin down?' },
        ] },
        { id: 'pending_approvals', title: 'Pending approvals', availability: 'ok', items: [
          { id: 'pa1', title: 'Reorder PO — Delta Foods ($18,400)', meta: 'Drafted by Lola · 2h ago', href: '/work' },
          { id: 'pa2', title: 'Payment reminders (3)', meta: 'Drafted by Lola · today', ask: 'Which invoices are overdue?' },
        ] },
        { id: 'next_actions', title: 'Suggested next actions', availability: 'ok', items:
          role === 'sales_rep'
            ? [{ id: 'nx-s1', title: 'Draft a check-in for Coastal Spirits', ask: 'Which of my accounts need a call?' }]
            : role === 'back_office'
              ? [{ id: 'nx-b1', title: 'Open a vendor claim for PO-2287', ask: 'Any receiving exceptions today?' }]
              : [
                  { id: 'nx1', title: 'Send reminders for the 3 oldest invoices', ask: 'Which invoices are overdue?' },
                  { id: 'nx2', title: 'Tell Sunrise about the new delivery date', ask: 'Where is container MSKU 448812?' },
                ] },
        { id: 'recent_tasks', title: 'Recently completed', availability: 'ok', items: [
          { id: 'rt1', title: 'Weekly sales report sent', meta: 'Verified · 08:02', severity: 'good' },
          { id: 'rt2', title: 'Inventory sync', meta: 'Verified · 07:45', severity: 'good' },
        ] },
      ],
    };
    return filterBriefingForRole(b, role);
  }
}

/** Same card shell, no values: an outage must never render stale numbers as current. */
export function blanked(c: EvidenceCard, note: string): EvidenceCard {
  // Drop timestamps: an unavailable source has no "as of".
  const base = { availability: 'unavailable' as const, availability_note: note, evidence: c.evidence?.map((e) => ({ source: e.source, href: e.href })) };
  switch (c.kind) {
    case 'metrics': return { ...c, ...base, metrics: [] };
    case 'chart': return { ...c, ...base, data: [] };
    case 'table': return { ...c, ...base, rows: [], total_label: undefined };
    case 'shipment_timeline': return { ...c, ...base, steps: [], exceptions: [] };
    case 'financial_explanation': return { ...c, ...base, drivers: [], headline: '' };
  }
}
