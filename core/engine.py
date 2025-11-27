import asyncio
import logging
import signal
from core.event_bus import EventBus

class Engine:
    def __init__(self):
        self.event_bus = EventBus()
        self.components = []
        self.running = False
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Handle graceful shutdown on Ctrl+C."""
        # Note: In Windows, signal handling with asyncio can be tricky.
        # This is a basic implementation.
        pass

    def register_component(self, component):
        """Registers a component to be managed by the engine."""
        self.components.append(component)
        if hasattr(component, 'set_event_bus'):
            component.set_event_bus(self.event_bus)

    async def start(self):
        """Starts the engine and all components."""
        self.running = True
        logging.info("Engine starting...")

        # Start Event Bus
        bus_task = asyncio.create_task(self.event_bus.run())

        # Start Components
        component_tasks = []
        for component in self.components:
            if hasattr(component, 'start'):
                # If start is async, await it or create task?
                # Usually components have a run loop or just initialization.
                # We assume components have a 'run' method that is an async task
                if hasattr(component, 'run') and asyncio.iscoroutinefunction(component.run):
                    component_tasks.append(asyncio.create_task(component.run()))
                elif asyncio.iscoroutinefunction(component.start):
                    await component.start()
                else:
                    component.start()

        logging.info("Engine running. Press Ctrl+C to stop.")

        try:
            # Keep main loop alive
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logging.info("Engine cancelled.")
        finally:
            await self.shutdown(bus_task, component_tasks)

    async def shutdown(self, bus_task, component_tasks):
        """Graceful shutdown."""
        logging.info("Shutting down...")
        self.running = False
        self.event_bus.stop()
        
        # Cancel all component tasks
        for task in component_tasks:
            task.cancel()
        
        if bus_task:
            bus_task.cancel()
            
        # Stop components
        for component in self.components:
            if hasattr(component, 'stop'):
                if asyncio.iscoroutinefunction(component.stop):
                    await component.stop()
                else:
                    component.stop()
        
        logging.info("Shutdown complete.")

    def stop(self):
        self.running = False
