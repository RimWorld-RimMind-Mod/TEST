using RimMindTestMod;
using Xunit;

namespace RimMindTestMod.Tests;

public class CalculatorTests
{
    [Fact]
    public void Add_TwoNumbers_ReturnsSum()
    {
        Assert.Equal(5, Calculator.Add(2, 3));
    }

    [Fact]
    public void Subtract_TwoNumbers_ReturnsDifference()
    {
        Assert.Equal(1, Calculator.Subtract(3, 2));
    }
}
