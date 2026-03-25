import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { BottomTabScreenProps } from '@react-navigation/bottom-tabs';

// Root stack (auth gate)
export type RootStackParamList = {
  Login: undefined;
  App: undefined;
};

// Bottom tab navigator (7 tabs)
export type AppTabParamList = {
  Dashboard: undefined;
  Chat: { conversationId?: string } | undefined;
  Reports: undefined;
  Accounts: undefined;
  Inventory: undefined;
  Logistics: undefined;
  Approvals: undefined;
};

// Accounts stack (supports drill-down to AccountDetail)
export type AccountsStackParamList = {
  AccountsList: undefined;
  AccountDetail: { accountId: string; accountName: string };
};

export type LoginScreenProps = NativeStackScreenProps<RootStackParamList, 'Login'>;
export type DashboardScreenProps = BottomTabScreenProps<AppTabParamList, 'Dashboard'>;
export type ChatScreenProps = BottomTabScreenProps<AppTabParamList, 'Chat'>;
export type ReportsScreenProps = BottomTabScreenProps<AppTabParamList, 'Reports'>;
export type InventoryScreenProps = BottomTabScreenProps<AppTabParamList, 'Inventory'>;
export type LogisticsScreenProps = BottomTabScreenProps<AppTabParamList, 'Logistics'>;
export type ApprovalsScreenProps = BottomTabScreenProps<AppTabParamList, 'Approvals'>;
