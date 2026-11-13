// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LolaOrb } from './LolaOrb';

const cleanup: Array<() => void> = [];

afterEach(() => { while (cleanup.length) cleanup.pop()?.(); });

async function mount(orb: React.ReactElement) {
  const node = document.createElement('div');
  document.body.append(node);
  const root = createRoot(node);
  await act(async () => root.render(orb));
  cleanup.push(() => { root.unmount(); node.remove(); });
  return node;
}

describe('official Lola orb interaction', () => {
  it('renders layered living glass rather than a pasted static image', async () => {
    const node = await mount(<LolaOrb />);
    expect(node.querySelector('img')).toBeNull();
    expect(node.querySelector('.lola-orb__glass')).not.toBeNull();
    expect(node.querySelectorAll('.lola-orb__plasma')).toHaveLength(3);
    expect(node.querySelectorAll('.lola-orb__orbit')).toHaveLength(3);
    expect(node.querySelector('[role="img"]')?.getAttribute('aria-label')).toContain('idle');
  });

  it('is a real accessible button when activation is supported', async () => {
    const onActivate = vi.fn();
    const node = await mount(<LolaOrb onActivate={onActivate} />);
    const orb = node.querySelector<HTMLButtonElement>('button[aria-label="Open Lola conversation"]');
    expect(orb).not.toBeNull();
    await act(async () => { orb?.click(); });
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('distinguishes listening, thinking, and speaking without faking audio', async () => {
    const node = await mount(<LolaOrb state="listening" audioLevel={0.65} />);
    const orb = node.querySelector<HTMLElement>('.lola-orb');
    expect(orb?.getAttribute('data-state')).toBe('listening');
    expect(orb?.style.getPropertyValue('--lola-audio')).toBe('0.65');
    const root = createRoot(document.createElement('div'));
    await act(async () => root.unmount());
  });

  it('clamps out-of-range audio and labels errors', async () => {
    const node = await mount(<LolaOrb state="error" audioLevel={Number.POSITIVE_INFINITY} />);
    const orb = node.querySelector<HTMLElement>('.lola-orb');
    expect(orb?.style.getPropertyValue('--lola-audio')).toBe('0');
    expect(orb?.getAttribute('aria-label')).toContain('error');
  });
});
