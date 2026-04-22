import { Tabs } from 'expo-router';
import { I } from '../components/bricks/Primitives';
import { T } from '../constants/Theme';

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ 
      headerShown: false,
      tabBarStyle: { 
        backgroundColor: T.night,
        borderTopWidth: 1,
        borderTopColor: T.nightHair,
      },
      tabBarActiveTintColor: T.moon,
      tabBarInactiveTintColor: T.moonMute,
    }}>
      <Tabs.Screen name="index" options={{ 
        title: 'Today',
        tabBarIcon: ({ color }) => <I name="home" color={color} size={19} /> 
      }} />
      <Tabs.Screen name="schedule" options={{ 
        title: 'Week',
        tabBarIcon: ({ color }) => <I name="cal" color={color} size={19} /> 
      }} />
      <Tabs.Screen name="uploads" options={{ 
        title: 'Upload',
        tabBarIcon: ({ color }) => <I name="up" color={color} size={19} /> 
      }} />
      <Tabs.Screen name="payouts" options={{ 
        title: 'Pay',
        tabBarIcon: ({ color }) => <I name="dol" color={color} size={19} /> 
      }} />
    </Tabs>
  );
}
