import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as sdk from '@microsoft/rayfin-client';
import {
  ensureSignedInWithFabric, initEmbeddedAuth,
} from '@microsoft/rayfin-auth-provider-fabric';
import type { AppSchema } from '../../rayfin/data/schema';
import type { AppFunctionsSchema } from '../../rayfin/functions/src/types';
import { createClient, initializeOperator, signIn } from '../client';

vi.mock('@microsoft/rayfin-client', async importOriginal => {
  const actual = await importOriginal<typeof import('@microsoft/rayfin-client')>();
  return { ...actual, resolveRayfinConfig: vi.fn() };
});

vi.mock('@microsoft/rayfin-auth-provider-fabric', () => ({
  ensureSignedInWithFabric: vi.fn(),
  initEmbeddedAuth: vi.fn(),
  signInWithBrokeredToken: vi.fn(),
}));

const tenantId = '5551f16b-831c-46a7-a226-4aae2ddca51a';
const clients: sdk.RayfinClient<AppSchema, AppFunctionsSchema>[] = [];

function client(config: sdk.RayfinRuntimeConfig) {
  const value = new sdk.RayfinClient<AppSchema, AppFunctionsSchema>({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test',
    authStorage: false, runtimeConfig: {
      workspaceId: 'workspace', itemId: 'item',
      portalUrl: 'https://app.fabric.microsoft.com',
      ...config,
    },
  });
  clients.push(value);
  return value;
}

beforeEach(() => {
  vi.stubEnv('DEV', false);
  vi.mocked(initEmbeddedAuth).mockResolvedValue(null);
});
afterEach(() => {
  clients.splice(0).forEach(value => value.auth.destroy());
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

it('routes browser sign-in to the deployment tenant without changing workspace or item', () => {
  const value = client({ tenantId });
  signIn(value);
  expect(ensureSignedInWithFabric).toHaveBeenCalledWith(value.auth, {
    workspaceId: 'workspace', projectId: 'item',
    fabricPortalUrl: `https://app.fabric.microsoft.com/?ctid=${tenantId}`,
    returnOrigin: window.location.origin,
  });
});

it('preserves portal paths and query parameters while replacing a stale tenant hint', () => {
  const value = client({
    tenantId, portalUrl: 'https://app.fabric.microsoft.com/custom?experience=power-bi&ctid=old',
  });
  signIn(value);
  const options = vi.mocked(ensureSignedInWithFabric).mock.calls[0][1];
  const portal = new URL(options.fabricPortalUrl);
  expect(portal.pathname).toBe('/custom');
  expect(portal.searchParams.get('experience')).toBe('power-bi');
  expect(portal.searchParams.getAll('ctid')).toEqual([tenantId]);
});

it('uses the same tenant-aware options when initializing embedded sign-in', async () => {
  const value = client({ tenantId });
  expect(await initializeOperator(value)).toBe(false);
  expect(initEmbeddedAuth).toHaveBeenCalledWith(value.auth, expect.objectContaining({
    fabricPortalUrl: `https://app.fabric.microsoft.com/?ctid=${tenantId}`,
  }));
});

it('retains the tenant in the actual SDK broker popup URL', async () => {
  const value = client({ tenantId });
  signIn(value);
  const options = vi.mocked(ensureSignedInWithFabric).mock.calls[0][1];
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  const actual = await vi.importActual<typeof import('@microsoft/rayfin-auth-provider-fabric')>(
    '@microsoft/rayfin-auth-provider-fabric',
  );
  await expect(actual.initiateFabricLogin(value.auth, options)).rejects.toThrow();
  const url = new URL(String(open.mock.calls[0][0]));
  expect(url.pathname).toBe('/secureItemEmbed');
  expect(url.searchParams.get('ctid')).toBe(tenantId);
  expect(url.searchParams.get('workspaceId')).toBe('workspace');
  expect(url.searchParams.get('itemId')).toBe('item');
});

it('preserves existing portal routing for older deployments without tenant metadata', () => {
  signIn(client({ portalUrl: 'https://app.fabric.microsoft.com/?ctid=legacy&experience=power-bi' }));
  const options = vi.mocked(ensureSignedInWithFabric).mock.calls[0][1];
  const portal = new URL(options.fabricPortalUrl);
  expect(portal.searchParams.get('ctid')).toBe('legacy');
  expect(portal.searchParams.get('experience')).toBe('power-bi');
});

it('passes the build-time tenant to runtime configuration resolution', async () => {
  vi.stubEnv('VITE_FABRIC_TENANT_ID', tenantId);
  const resolve = vi.mocked(sdk.resolveRayfinConfig).mockResolvedValue({
    baseUrl: 'https://example.invalid/', publishableKey: 'pk-test',
    runtimeConfig: { tenantId },
  });
  const value = await createClient();
  clients.push(value);
  expect(resolve).toHaveBeenCalledWith(expect.objectContaining({ tenantId }));
  expect(value.runtimeConfig?.tenantId).toBe(tenantId);
});
