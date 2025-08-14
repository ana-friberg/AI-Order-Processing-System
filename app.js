const connectDB = require('./utils/db');
const AgilentOrder = require('./models/agilent.schema');

// Connect to MongoDB
connectDB();

// Example usage
const saveOrder = async (orderData) => {
    try {
        const order = new AgilentOrder(orderData);
        const savedOrder = await order.save();
        console.log('Order saved successfully:', savedOrder);
        return savedOrder;
    } catch (error) {
        console.error('Error saving order:', error);
        throw error;
    }
};