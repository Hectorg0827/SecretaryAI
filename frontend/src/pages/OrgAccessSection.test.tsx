import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { OrgAccessSection, describeAccess, type OrgMember } from './OrgAccessSection';
import { api } from '../lib/api';

const owner: OrgMember = {
  user_id: 'u-owner', name: 'Olivia Owner', email: 'o@t.test', role: 'owner', active: true,
  profile: null, reports_to_name: null, allowed_levels: ['executive'],
};
const mgr: OrgMember = {
  user_id: 'u-mgr', name: 'Sam Manager', email: 's@t.test', role: 'manager', active: true,
  profile: { org_level: 'department_manager', department: 'sales', manager_user_id: 'u-owner', territories: [],
             financial_clearance: false, hr_clearance: false, action_limit: '5000' },
  reports_to_name: 'Olivia Owner', allowed_levels: ['department_manager', 'employee', 'executive'],
};
const rep: OrgMember = {
  user_id: 'u-rep', name: 'Rita Rep', email: 'r@t.test', role: 'sales_rep', active: true,
  profile: { org_level: 'employee', department: 'sales', manager_user_id: 'u-mgr', territories: ['West'],
             financial_clearance: false, hr_clearance: false, action_limit: null },
  reports_to_name: 'Sam Manager', allowed_levels: ['employee'],
};
const gone: OrgMember = { ...rep, user_id: 'u-gone', name: 'Ida Inactive', active: false, profile: null };
const members = [owner, mgr, rep, gone];
const response = { members, departments: ['general', 'sales', 'finance', 'hr'],
                   levels: ['executive', 'department_manager', 'employee'] };

beforeEach(() => { vi.spyOn(api, 'get').mockResolvedValue(response as never); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('describeAccess', () => {
  it('explains defaults and configured access in plain language', () => {
    expect(describeAccess(owner, members)).toContain('whole company');
    expect(describeAccess({ ...rep, profile: null }, members)).toContain('only accounts assigned');
    const text = describeAccess(mgr, members);
    expect(text).toContain('Department manager');
    expect(text).toContain('reports to Olivia Owner');
    expect(text).toContain('approval limit $5,000');
  });
});

describe('OrgAccessSection', () => {
  it('lists people and never offers editing for disabled accounts', async () => {
    render(<OrgAccessSection />);
    expect(await screen.findByText(/Rita Rep/)).toBeTruthy();
    expect(screen.getByText(/territories: West/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit access for Ida Inactive' })).toBeNull();
  });

  it('only offers levels the role allows, and never the person themselves as manager', async () => {
    render(<OrgAccessSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit access for Rita Rep' }));
    const level = screen.getByLabelText('Level') as HTMLSelectElement;
    expect([...level.options].map((o) => o.value)).toEqual(['employee']);
    const reportsTo = screen.getByLabelText('Reports to') as HTMLSelectElement;
    const options = [...reportsTo.options].map((o) => o.value);
    expect(options).not.toContain('u-rep');
    expect(options).not.toContain('u-gone');
  });

  it('saves cleaned territories and limit, then reloads', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue({} as never);
    render(<OrgAccessSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit access for Rita Rep' }));
    fireEvent.change(screen.getByLabelText('Territories'), { target: { value: ' West, , Northeast ' } });
    fireEvent.change(screen.getByLabelText(/Approval limit/), { target: { value: '2500' } });
    fireEvent.click(screen.getByLabelText(/Finance/));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    const [path, body] = put.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe('/api/lola/org/u-rep');
    expect(body.territories).toEqual(['West', 'Northeast']);
    expect(body.action_limit).toBe('2500');
    expect(body.financial_clearance).toBe(true);
    expect(await screen.findByRole('status')).toBeTruthy();
  });

  it("shows the server's reason when a change is refused", async () => {
    vi.spyOn(api, 'put').mockRejectedValue(new Error('That would create a reporting loop'));
    render(<OrgAccessSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit access for Sam Manager' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toContain('reporting loop');
  });

  it('rejects a malformed approval limit before calling the server', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue({} as never);
    render(<OrgAccessSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit access for Rita Rep' }));
    fireEvent.change(screen.getByLabelText(/Approval limit/), { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toContain('amount like 5000');
    expect(put).not.toHaveBeenCalled();
  });

  it('can reset a configured person to role defaults', async () => {
    const del = vi.spyOn(api, 'delete').mockResolvedValue({} as never);
    render(<OrgAccessSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit access for Sam Manager' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset to role defaults' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/api/lola/org/u-mgr'));
  });

  it('reports a load failure instead of an empty org chart', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('Only a company owner can manage the org chart'));
    render(<OrgAccessSection />);
    expect((await screen.findByRole('alert')).textContent).toContain('Only a company owner');
  });
});
