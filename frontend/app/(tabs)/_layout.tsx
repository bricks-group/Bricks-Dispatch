import { Tabs } from 'expo-router';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { Briefcase, Calendar, Upload, User } from 'lucide-react-native';

type TabIconProps = {
  Icon: React.ComponentType<{ size: number; color: string; strokeWidth: number }>;
  label: string;
  focused: boolean;
};

function TabIcon({ Icon, label, focused }: TabIconProps) {
  return (
    <View style={styles.tabItem}>
      {focused && <View style={styles.activeIndicator} />}
      <Icon
        size={20}
        color={focused ? '#FFFFFF' : '#565B7E'}
        strokeWidth={focused ? 2.5 : 1.8}
      />
      <Text style={[styles.tabLabel, focused && styles.tabLabelActive]}>
        {label}
      </Text>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarShowLabel: false,
        tabBarActiveTintColor: '#FFFFFF',
        tabBarInactiveTintColor: '#565B7E',
        sceneStyle: { backgroundColor: '#0C0D14' },
        ...(Platform.OS === 'web' ? { unmountOnBlur: true } : {}),
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Jobs',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Briefcase} label="Jobs" focused={focused} />
          ),
          tabBarTestID: 'tab-jobs',
        }}
      />
      <Tabs.Screen
        name="schedule"
        options={{
          title: 'Schedule',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Calendar} label="Schedule" focused={focused} />
          ),
          tabBarTestID: 'tab-schedule',
        }}
      />
      <Tabs.Screen
        name="uploads"
        options={{
          title: 'Uploads',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Upload} label="Uploads" focused={focused} />
          ),
          tabBarTestID: 'tab-uploads',
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={User} label="Profile" focused={focused} />
          ),
          tabBarTestID: 'tab-profile',
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: '#0C0D14',
    borderTopWidth: 1,
    borderTopColor: '#1E2030',
    height: 76,
    paddingTop: 8,
    paddingBottom: 14,
    elevation: 0,
    shadowOpacity: 0,
  },
  tabItem: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 5,
    position: 'relative',
  },
  activeIndicator: {
    position: 'absolute',
    top: -12,
    width: 24,
    height: 3,
    borderRadius: 2,
    backgroundColor: '#3B82F6',
  },
  tabLabel: {
    fontSize: 11,
    fontWeight: '500',
    color: '#565B7E',
    letterSpacing: 0.3,
  },
  tabLabelActive: {
    color: '#FFFFFF',
    fontWeight: '600',
  },
});
