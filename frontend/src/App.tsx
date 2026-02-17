import { RouterProvider, createRouter, Route, RootRoute } from '@tanstack/react-router'
import { Dashboard } from '@/pages/Dashboard'
import { Zones } from '@/pages/Zones'
import { Settings } from '@/pages/Settings'
import { Chat } from '@/pages/Chat'
import { Analytics } from '@/pages/Analytics'
import { Layout } from '@/components/layout/Layout'

const rootRoute = new RootRoute({
  component: Layout,
})

const dashboardRoute = new Route({ getParentRoute: () => rootRoute, path: '/', component: Dashboard })
const zonesRoute = new Route({ getParentRoute: () => rootRoute, path: '/zones', component: Zones })
const settingsRoute = new Route({ getParentRoute: () => rootRoute, path: '/settings', component: Settings })
const chatRoute = new Route({ getParentRoute: () => rootRoute, path: '/chat', component: Chat })
const analyticsRoute = new Route({ getParentRoute: () => rootRoute, path: '/analytics', component: Analytics })

const routeTree = rootRoute.addChildren([dashboardRoute, zonesRoute, settingsRoute, chatRoute, analyticsRoute])

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

function App() {
  return <RouterProvider router={router} />
}

export default App
